import time

import numpy as np

from ..cheminf.utils import UnsupportedClassifier, ModeError
from ..cheminf.controller import ChemInfController


class ChemInfOperator(object):
    def __init__(self):
        self.controller = ChemInfController()
        self.mode = self.controller.args.mode
        self.classifier = self.controller.args.classifier
        self.__start_time = time.time()

        if self.mode == 'postproc':
            from ..cheminf.postprocessing import PostProcNormal
            self.postproc = PostProcNormal(self.controller)

        elif self.mode == 'preproc':
            from ..cheminf.preprocessing import PreProcNormal
            self.preproc = PreProcNormal(self.controller)

        elif self.mode == 'auto':
            from ..cheminf.preprocessing import PreProcAuto
            self.preproc = PreProcAuto(self.controller)
            if self.controller.config.execute.auto_plus_sum or self.controller.config.execute.auto_plus_plot:
                from ..cheminf.postprocessing import PostProcAuto
                self.postproc = PostProcAuto(self.controller)
            else:
                self.postproc = None

        if self.mode in self.controller.model_modes or self.mode in self.controller.auto_modes:
            if self.controller.args.classifier == 'all':
                models = self.init_model(self.controller)
                self.rndfor_model = models[0]
                self.nn_model = models[1]

            else:
                if self.controller.args.classifier == 'rndfor':
                    self.rndfor_model = self.init_model(self.controller)
                    self.nn_model = None

                elif self.controller.args.classifier == 'nn':
                    self.nn_model = self.init_model(self.controller)
                    self.rndfor_model = None

                else:
                    raise UnsupportedClassifier(self.controller.args.classifier)

    def __del__(self):
        try:
            print(f"\nChemInf Runtime\n-----------------------------------\nRuntime for was {self.run_time()}s")
        except AttributeError:
            pass

    def run_time(self):
        current_time = time.time()
        return np.round(current_time - self.__start_time, decimals=2)

    @staticmethod
    def init_model(controller):
        if controller.args.classifier == 'all':
            from ..cheminf.models import ModelRNDFOR
            from ..cheminf.models import ModelNN
            models = [ModelRNDFOR(controller), ModelNN(controller)]
            return models

        elif controller.args.classifier == 'rndfor':
            from ..cheminf.models import ModelRNDFOR
            model = ModelRNDFOR(controller)

        elif controller.args.classifier == 'nn':
            from ..cheminf.models import ModelNN
            model = ModelNN(controller)

        else:
            raise UnsupportedClassifier(controller.args.classifier)

        return model

    def get_model(self, classifier=None):
        if classifier is None:
            classifier = self.classifier

        return getattr(self, f"{classifier}_model")

    def get_utils(self, utils=None):
        if utils is None:
            utils = self.mode
        return getattr(self, utils)

    # Todo: Make classifier option 'all' functional
    def run(self):
        if self.classifier:
            if self.classifier == 'all':
                classifiers = self.controller.classifier_types
            else:
                classifiers = [self.classifier]
        else:
            classifiers = [None]

        if self.mode in self.controller.auto_modes:
            for classifier in classifiers:
                model = self.get_model(classifier)
                model.build()
                model.predict()

        if self.mode == 'postproc' or self.mode in self.controller.auto_modes:
            self.postproc.run()

        elif self.mode == 'preproc':
            self.preproc.run()

        elif self.mode in self.controller.model_modes:
            for classifier in classifiers:
                model = self.get_model(classifier)
                model_activate = model.get(self.mode)
                model_activate()
        else:
            raise ModeError('operator', self.mode)


def run_operator():
    cheminf = ChemInfOperator()
    cheminf.run()
