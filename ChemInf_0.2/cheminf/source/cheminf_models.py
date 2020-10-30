import os
import sys

import cloudpickle
import numpy as np

from source.cheminf_classifier import ChemInfClassifier
from source.cheminf_data_utils import split_array


class ChemInfModel(object):
    def __init__(self, database, model_type):
        self.in_file = database.args.in_file
        self.type = model_type
        self.path = database.path
        self.config = database.config
        self.name = database.args.name
        self.mode = database.args.mode
        if not database.args.models_dir:
            self.models_dir = f"{self.path}/data/amcp_models/{self.name}"
        else:
            self.models_dir = database.args.models_dir
        try:
            os.mkdir(self.models_dir)
            print(f"Created the model directory: {self.models_dir}")
        except FileExistsError:
            pass
        if hasattr(database.args, 'out_file'):
            if not database.args.out_file:
                self.out_file = f"{self.path}/data/amcp_predictions/{self.name}/" \
                                f"amcp_{self.name}_{self.type}_{self.mode}.csv"
            else:
                self.out_file = database.args.out_file
            try:
                os.mkdir(os.path.dirname(self.out_file))
                print(f"Created the model directory: {os.path.dirname(self.out_file)}")
            except FileExistsError:
                pass
        self.classifier = ChemInfClassifier(self.type, self.config)

    def improve(self):
        if self.build():
            models = self.load_models()
            self.build(models)
        else:
            raise NotImplementedError("The the improve method needs to be inherent to work")

    def save_models(self, model=None, iteration=0):
        if model is None:
            model = self.classifier.architecture
        model_name = f"{self.models_dir}/amcp_{self.name}_{self.type}_m{iteration}.z"
        if os.path.isfile(model_name):
            os.remove(model_name)
        with open(model_name, mode='ab') as f:
            cloudpickle.dump(model, f)

    def save_scores(self, calibration_data, iteration=0, model=None):
        if model is None:
            model = self.classifier.architecture
        for c, alpha_c in enumerate(model.cali_nonconf_scores(calibration_data)):
            model_score = f"{self.models_dir}/amcp_{self.name}_{self.type}_calibration-α{c}_m{iteration}.z"
            if os.path.isfile(model_score):
                os.remove(model_score)
            with open(model_score, mode='ab') as f:
                cloudpickle.dump(alpha_c, f)

    def load_models(self):
        dir_files = os.listdir(self.models_dir)
        nr_models = sum([1 for f in dir_files if f.startswith(f"amcp_{self.name}_{self.type}_m")])
        models = []
        print(f"Loading models from {self.models_dir}")
        for i in range(nr_models):
            model_file = f"{self.models_dir}/amcp_{self.name}_{self.type}_m{i}.z"
            with open(model_file, 'rb') as f:
                models.append(cloudpickle.load(f))

        print("Loaded {nr_models} models.".format(nr_models=nr_models))
        return models

    def load_scores(self):
        # Number of classes is amount of model files, divided by model count
        #  minus 1, as 1 file is classification file
        nr_models = self.config.nr_models
        nr_model_files = sum([1 for f in os.listdir(self.models_dir) if f.endswith(".z")])
        nr_class = int((nr_model_files / nr_models) - 1)
        scores = [list() for _ in range(nr_class)]
        print(f"Loading scores from {self.models_dir}")
        for i in range(nr_models):
            for c in range(nr_class):
                score_file = f"{self.models_dir}/amcp_{self.name}_{self.type}_calibration-α{c}_m{i}.z"
                with open(score_file, 'rb') as f:
                    scores[c].append(cloudpickle.load(f))

        print(f"Loaded {nr_models} x {nr_class} scores.")
        return scores, nr_class

    def get(self):
        return self.classifier.get()

    def create_new(self):
        return self.classifier.new()

    def reset(self):
        self.classifier.reset()


class ModelRNDFOR(ChemInfModel):
    def __init__(self, database):
        super(ModelRNDFOR, self).__init__(database, 'rndfor')
        if hasattr(database.args, 'out_file2'):
            self.out_file_train = database.args.out_file2

    def build(self):
        """Trains NR_MODELS models and saves them as compressed files
        in the AMCP_MODELS_PATH directory along with the calibration
        conformity scores.
        """
        from source.cheminf_data_utils import shuffle_arrays_in_unison, read_array

        nr_models = self.config.nr_models

        prop_train_ratio = self.config.prop_train_ratio
        data_type = self.config.data_type

        training_id, training_data = read_array(self.in_file, data_type)
        nr_of_training_samples = len(training_id)
        for model_iteration in range(nr_models):
            self.reset()
            model = self.classifier.architecture
            # Shuffling the sample_IDs and data of the training
            #  set in unison, without creating copies.
            shuffle_arrays_in_unison(training_id, training_data)
            # Splitting training data into proper train set and
            #  calibration set.
            prop_train_data, calibration_data = split_array(training_data,
                                                            array_size=nr_of_training_samples,
                                                            percent_to_first=prop_train_ratio)

            print(f"Now building model: {model_iteration}")
            model.fit(prop_train_data[:, 1:], prop_train_data[:, 0])
            # Saving models to disk.
            self.save_models(model_iteration)

            # Retrieving the calibration conformity scores.
            self.save_scores(calibration_data, model_iteration)

    def predict(self):
        """Reads the pickled models and calibration conformity scores.
        Initializes a fixed size numpy array. Reads in nrow lines of the
        input file and then predicts the samples in the batch with each
        ml_model. The median p-values are calculated and written out in the
        out_file. This is performed until all lines in the in_file are predicted.
        """
        out_file_path, out_file = os.path.split(self.out_file)
        if not os.path.isdir(out_file_path):
            os.mkdir(out_file_path)

        # Reading parameters
        nrow = self.config.pred_nrow  # To control memory.

        dir_files = os.listdir(self.models_dir)
        nr_of_models = sum([1 for f in dir_files if f.startswith(f"amcp_{self.name}_rndfor")])

        # Initializing list of pointers to model objects
        #  and calibration conformity score lists.
        models = self.load_models()
        calibration_alphas_c, nr_class = self.load_scores()

        # read (compressed) features
        file_name, extension = os.path.splitext(self.in_file)
        if extension == '.bz2':
            import bz2
            fin = bz2.open(self.in_file, 'rb')
        elif extension == '.gz':
            import gzip
            fin = gzip.open(self.in_file, 'rb')
        else:
            fin = open(self.in_file, 'r')

        with open(os.path.join(out_file_path, out_file), 'w+') as fout:
            # Getting dimensions for np.array allocation.
            if extension == '.gz' or extension == '.bz2':
                ncol = len(fin.readline().decode().split()) - 1
            else:
                ncol = len(fin.readline().split()) - 1
            fin.seek(0)
            sample_id = np.empty(nrow, dtype=np.dtype('U50'))
            data_type = self.config.get('data_type')
            if data_type == 'integer':
                predict_data = np.empty((nrow, ncol), dtype=int)
            elif data_type == 'float':
                predict_data = np.empty((nrow, ncol), dtype=float)

            # Three dimensional class array
            p_c_array = np.empty((nrow, nr_of_models, nr_class), dtype=float)

            class_string = "\t".join(['p(%d)' % c for c in range(nr_class)])
            fout.write(f"amcp_prediction\tpredict_file:\"{self.in_file}\"\n"
                       f"sampleID\treal_class\t{class_string}\n")
            print(f"Allocated memory for an {nrow} X {ncol} array.")

            for i, line in enumerate(fin):
                # Reading in nrow samples.
                if extension == '.gz' or extension == '.bz2':
                    line = line.decode()  # Bin to str conversion.
                if i % nrow != nrow - 1:
                    # Inserting data from the in_file into np.arrays.
                    (sample_id[i % nrow],
                     sample_data) = line.strip().split(None, 1)
                    predict_data[i % nrow] = sample_data.split()
                else:
                    (sample_id[i % nrow],
                     sample_data) = line.strip().split(None, 1)
                    predict_data[i % nrow] = sample_data.split()
                    # The array has now been filled.
                    for model_index, model in enumerate(models):
                        # Predicting and getting p_values for each model
                        #  and sample.
                        predict_alphas = model.nonconformity_scores(predict_data[:, 1:])

                        # Iterate over the classes
                        for c, predict_alpha_c in enumerate(zip(*predict_alphas)):
                            for sample_index in range(nrow):
                                p_c = model.get_CP_p_value(predict_alpha_c[sample_index],
                                                           calibration_alphas_c[c][model_index])

                                p_c_array[sample_index, model_index, c] = p_c

                        # Calculating median p for each sample in the array, class c
                        p_c_medians = np.median(p_c_array, axis=1)

                    # Writing out sample prediction.
                    print(f"Predicted samples: {i + 1}.")
                    for j in range(nrow):
                        p_c_string = "\t".join([str(p_c_medians[j, c]) for c in range(nr_class)])
                        fout.write(f"{sample_id[j]}\t"
                                   f"{predict_data[j, 0]}\t"
                                   f"{p_c_string}\n")

            # After all samples have been read, the array still contains some
            #  samples that have not been predicted, as the 'else' was never
            #  encountered. These samples are handled and written out below.
            if i % nrow != nrow - 1:
                for model_index, model in enumerate(models):
                    predict_alphas = model.nonconformity_scores(predict_data[:i % nrow + 1, 1:])

                    # Iterate over the classes
                    for c, predict_alpha_c in enumerate(zip(*predict_alphas)):

                        for sample_index in range(i % nrow + 1):
                            p_c = model.get_CP_p_value(predict_alpha_c[sample_index],
                                                       calibration_alphas_c[c][model_index])

                            p_c_array[sample_index, model_index, c] = p_c

                # Calculate remaining p-value medians.
                p_c_medians = np.median(p_c_array[:i % nrow + 1, :], axis=1)

                # Writing out final samples predictions.
                for i in range(i % nrow + 1):
                    p_c_string = "\t".join([str(p_c_medians[i, c]) for c in range(nr_class)])
                    fout.write(f"{sample_id[i]}\t"
                               f"{predict_data[i, 0]}\t"
                               f"{p_c_string}\n")
        fin.close()

    def validate(self):
        """Cross validation using the K-fold method.
        """
        from source.cheminf_data_utils import shuffle_arrays_in_unison
        from source.cheminf_data_utils import read_array

        # Reading parameters
        nr_models = self.config.nr_models
        prop_train_ratio = self.config.prop_train_ratio
        val_folds = self.config.val_folds

        # Reading in file to use for validation.
        data_type = self.config.data_type
        val_id, val_data = read_array(self.in_file, data_type)
        nr_of_val_samples = len(val_id)
        val_indices = np.array(range(nr_of_val_samples))
        nr_holdout_samples = int(nr_of_val_samples / val_folds)
        nr_class = len(np.unique(val_data[:, 0]))

        # Out file(s).
        fout_test = open(self.out_file, 'w+')
        class_string = "\t".join(['p(%d)' % c for c in range(nr_class)])
        fout_test.write(f"amcp_validation\ttest_samples\tvalidation_file:\"{self.in_file}\"\n"
                        f"sampleID\treal_class\t{class_string}\n")

        if self.out_file_train:
            fout_train = open(self.out_file_train, 'w+')
            fout_train.write(f"amcp_validation\ttrain_samples\tvalidation_file:\"{self.in_file}\"\n"
                             f"sampleID\treal_class\t{class_string}\n")

        for k in range(val_folds):
            print(f"\nBuilding and predicting for cross validation chunk {k}.")
            test_indices = val_indices[k * nr_holdout_samples:(k + 1) * nr_holdout_samples]

            training_indices = list(set(val_indices) - set(test_indices))

            test_data = val_data[test_indices]
            test_id = val_id[test_indices]
            nr_of_test_samples = len(test_id)

            training_data = val_data[training_indices]
            training_id = val_id[training_indices]
            nr_of_training_samples = len(training_id)
            print("Copied validation samples to training and test arrays")

            # Three dimensional class array. Initialize/reset.
            p_c_array = np.empty((nr_of_test_samples, nr_models, nr_class), dtype=float)
            if self.out_file_train:
                p_c_array_training = np.empty((nr_of_training_samples,
                                               nr_models, nr_class), dtype=float)
            print(f"Allocated memory for array.")

            for model_iteration in range(nr_models):
                self.reset()
                model = self.classifier.architecture
                # Shuffling the sample_IDs and data of the training
                #  set in unison, without creating copies.
                shuffle_arrays_in_unison(training_id, training_data)
                # Splitting training data into proper train set and
                #  calibration set.
                prop_train_data, calibration_data = split_array(training_data,
                                                                array_size=nr_of_training_samples,
                                                                percent_to_first=prop_train_ratio)

                print(f"Now building model: {model_iteration}")
                model.fit(prop_train_data[:, 1:], prop_train_data[:, 0])

                # Initializing/resetting calibration alpha lists.
                calibration_alphas_c = []

                # Retrieving the calibration conformity scores.
                for c, alpha_c in enumerate(model.cali_nonconf_scores(calibration_data)):
                    calibration_alphas_c.append(alpha_c)

                test_alphas = model.nonconformity_scores(test_data[:, 1:])
                if self.out_file_train:
                    training_alphas = model.nonconformity_scores(training_data[:, 1:])

                # Iterate over the classes.
                for c, test_alpha_c in enumerate(zip(*test_alphas)):
                    for sample_index in range(nr_of_test_samples):
                        p_c = model.get_CP_p_value(test_alpha_c[sample_index],
                                                   calibration_alphas_c[c])

                        p_c_array[sample_index, model_iteration, c] = p_c

                # Iterate over the classes for the training samples.
                if self.out_file_train:
                    for c, training_alpha_c in enumerate(zip(*training_alphas)):
                        for sample_index in range(nr_of_training_samples):
                            p_c = model.get_CP_p_value(training_alpha_c[sample_index],
                                                       calibration_alphas_c[c])

                            p_c_array_training[sample_index, model_iteration, c] = p_c

            # Calculating median p for each sample in the array, class c
            p_c_medians = np.median(p_c_array, axis=1)

            # Writing out sample prediction.
            for i in range(nr_of_test_samples):
                p_c_string = "\t".join([str(p_c_medians[i, c]) for c in range(nr_class)])
                fout_test.write(f"{test_id[i]}\t"
                                f"{test_data[i, 0]}\t"
                                f"{p_c_string}\n")

            # Calculating median p for each sample in the array, class c
            if self.out_file_train:
                p_c_medians_training = np.median(p_c_array_training, axis=1)
                for i in range(nr_of_training_samples):
                    p_c_string = "\t".join([str(p_c_medians_training[i, c]) for c in range(nr_class)])
                    fout_train.write(f"{training_id[i]}\t"
                                     f"{training_data[i, 0]}\t"
                                     f"{p_c_string}\n")

        fout_test.close()
        if self.out_file_train:
            fout_train.close()


class ModelNN(ChemInfModel):
    def __init__(self, database):
        super(ModelNN, self).__init__(database, 'nn')
        global read_dataframe
        _temp = __import__("source", globals(), locals(), ['amcp_data_utils.read_dataframe'])
        read_dataframe = _temp.amcp_data_utils.read_dataframe
    def make_train_test_dataset(self):
        from source.cheminf_data_utils import cut_file
        train_test_ratio = self.config.train_test_ratio
        dataframe = read_dataframe(self.in_file)
        return cut_file(dataframe, train_test_ratio, shuffle=True, split=True)

    def build(self):
        import torch
        from skorch import NeuralNetClassifier
        from skorch.callbacks import EarlyStopping, EpochScoring
        from skorch.dataset import Dataset
        from skorch.helper import predefined_split
        from torch import nn

        from libs.nonconformist.base import ClassifierAdapter
        from libs.nonconformist.icp import IcpClassifier
        from libs.nonconformist.nc import ClassifierNc, MarginErrFunc
        from libs.torchtools.optim import RangerLars

        train_dataframe = read_dataframe(self.in_file)
        nr_models = self.config.nr_models
        val_ratio = self.config.val_ratio
        cal_ratio = self.config.cal_ratio
        batch_size = self.config.batch_size
        max_epochs = self.config.max_epochs
        early_stop_patience = self.config.early_stop_patience
        early_stop_threshold = self.config.early_stop_threshold

        X = np.array(train_dataframe.iloc[:, 2:]).astype(np.float32)
        y = np.array(train_dataframe['class']).astype(np.int64)

        for i in range(nr_models):
            self.reset()
            classifier = self.classifier.architecture

            # Setup proper training, calibration and validation sets

            # validation set created
            total_train = np.arange(len(train_dataframe.index))
            valid_set, train_set = split_array(total_train, val_ratio, shuffle=True)

            # calib set and proper training set created
            calib_set, proper_train_set = split_array(train_set, cal_ratio, shuffle=True)

            # Convert validation to skorch dataset
            valid_dataset = Dataset(X[valid_set], y[valid_set])

            # Calculate number of training examples for each class (for weights)
            nr_class0 = len([x for x in y[proper_train_set] if x == 0])
            nr_class1 = len([x for x in y[proper_train_set] if x == 1])

            # Setup for class weights
            class_weights = 1 / torch.FloatTensor([nr_class0, nr_class1])

            # Define the skorch classifier
            minus_ba = EpochScoring(minus_bacc,
                                    name='-BA',
                                    on_train=False,
                                    use_caching=False,
                                    lower_is_better=True)

            early_stop = EarlyStopping(patience=early_stop_patience,
                                       threshold=early_stop_threshold,
                                       threshold_mode='rel',
                                       lower_is_better=True)

            model = NeuralNetClassifier(classifier, batch_size=batch_size, max_epochs=max_epochs,
                                        train_split=predefined_split(valid_dataset),  # Use predefined validation set
                                        optimizer=RangerLars,
                                        optimizer__lr=0.001,
                                        optimizer__weight_decay=0.1,
                                        criterion=nn.CrossEntropyLoss,
                                        criterion__weight=class_weights,
                                        callbacks=[minus_ba, early_stop])

            mem_info = f"\nMEMORY INFORMATION FOR MODEL: {i}\n" \
                       f"------------------------------------------------------\n" \
                       f"Tensor size: {sys.getsizeof(model)}\n" \
                       f"Element size: {class_weights.element_size()}\n" \
                       f"Number of elements: {class_weights.nelement()}\n" \
                       f"Element memory size: {(class_weights.nelement() * class_weights.element_size())}" \
                       f"\nBatch size: {model.batch_size}" \
                       f"\n------------------------------------------------------\n"

            print(mem_info)

            print(f"Working on model {i}")
            icp = IcpClassifier(ClassifierNc(ClassifierAdapter(model), MarginErrFunc()))
            icp.fit(X[proper_train_set], y[proper_train_set])
            icp.calibrate(X[calib_set], y[calib_set])

            self.save_models(model=icp, iteration=i)

    def predict(self):
        import pandas as pd

        test_dataframe = read_dataframe(self.in_file)
        nr_models = self.config.nr_models
        sig = self.config.pred_sig

        models = self.load_models()

        X = np.array(test_dataframe.iloc[:, 2:]).astype(np.float32)

        p_value_results = []
        for i in range(nr_models):
            icp = models[i]

            print(f"Predicting from model {i}")

            pred = icp.predict(X, significance=sig)
            pred = np.round(pred, 6).astype('str')

            p_value = {'P(0)': pred[:, 0].tolist(),
                       'P(1)': pred[:, 1].tolist()
                       }

            p_value_results.append(pd.DataFrame(p_value).astype('float32'))

        p_value_dataframe = pd.concat(p_value_results, axis=1).groupby(level=0, axis=1).median()
        results_dataframe = pd.concat([test_dataframe['id'], test_dataframe['class'], p_value_dataframe], axis=1)

        results_dataframe.to_csv(self.out_file, sep='\t', mode='w+', index=False, header=True)

    def validate(self):
        return None


def minus_bacc(net, X=None, y=None):
    from sklearn.metrics import balanced_accuracy_score

    y_true = y
    y_pred = net.predict(X)
    return -balanced_accuracy_score(y_true, y_pred)
