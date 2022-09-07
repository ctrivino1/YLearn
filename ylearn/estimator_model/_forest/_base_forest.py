# Some snippets of code are from scikit-learn

import numbers
import threading
import numpy as np
import pandas as pd

from abc import abstractmethod
from copy import deepcopy
from joblib import Parallel, delayed

from sklearn.utils import check_random_state

from ..utils import convert2array

from ..base_models import BaseEstModel
from sklearn.preprocessing import OrdinalEncoder

# we ignore the warm start and inference parts in the current version
class BaseForest:
    @abstractmethod
    def __init__(
        self,
        base_estimator,
        n_estimators=100,
        *,
        estimator_params=tuple(),
        n_jobs=None,
        random_state=None,
        warm_start=None,
        max_samples=None,
        class_weight=None,
    ):
        self.base_estimator = base_estimator
        self.n_estimators = n_estimators
        self.estimator_params = estimator_params

        self.random_state = random_state
        self.n_jobs = n_jobs
        self.warm_start = warm_start
        self.max_samples = max_samples
        self.class_weight = class_weight

    def _validate_estimator(self, default=None):
        if not isinstance(self.n_estimators, numbers.Integral):
            raise ValueError(
                f"n_estimators must be an integer, got {type(self.n_estimators)}."
            )

        if self.n_estimators <= 0:
            raise ValueError(
                f"n_estimators must be greater than zero, got {self.n_estimators}."
            )

        if self.base_estimator is not None:
            self.base_estimator_ = self.base_estimator
        else:
            self.base_estimator_ = default

        if self.base_estimator_ is None:
            raise ValueError("base_estimator cannot be None")

    def _make_estimator(self, append=True, random_state=None):
        estimator = deepcopy(self.base_estimator_)
        estimator.set_params(**{p: getattr(self, p) for p in self.estimator_params})

        if random_state is not None:
            random_state = check_random_state(random_state)

            to_set = {}
            for key in sorted(estimator.get_params(deep=True)):
                if key == "random_state" or key.endswith("__random_state"):
                    to_set[key] = random_state.randint(np.iinfo(np.int32).max)

            if to_set:
                estimator.set_params(**to_set)

        if append:
            self.estimators_.append(estimator)

        return estimator

    def __len__(self):
        return len(self.estimators_)

    def __getitem__(self, index):
        return self.estimators_[index]

    def __iter__(self):
        return iter(self.estimators_)


def _prediction(predict, w, v, v_train):
    pred = predict(w, v, return_node=False).reshape(-1, 1)
    y_pred = predict(w, v_train, return_node=True)
    y_test_pred, y_test_pred_num = [], []
    for p in y_pred:
        y_test_pred.append(p.value)
        y_test_pred_num.append(p.sample_num)

    return (y_test_pred == pred) / y_test_pred_num


class BaseCausalForest(BaseEstModel, BaseForest):
    def __init__(
        self,
        base_estimator,
        n_estimators=100,
        *,
        estimator_params=None,
        max_depth=None,
        min_samples_split=2,
        min_samples_leaf=1,
        min_weight_fraction_leaf=0.0,
        max_features=1.0,
        max_leaf_nodes=None,
        min_impurity_decrease=0.0,
        # bootstrap=True,
        # oob_score=False,
        n_jobs=None,
        random_state=None,
        verbose=0,
        warm_start=False,
        max_samples=None,
        categories="auto",
        is_discrete_treatment=True,
        is_discrete_outocme=False,
    ):
        if estimator_params is None:
            estimator_params = (
                "max_depth",
                "min_samples_split",
                "min_samples_leaf",
                "min_weight_fraction_leaf",
                "max_features",
                "max_leaf_nodes",
                "min_impurity_decrease",
                "random_state",
            )
        # TODO: modify the multiple inheritance
        BaseForest.__init__(
            self,
            base_estimator=base_estimator,
            n_jobs=n_jobs,
            n_estimators=n_estimators,
            estimator_params=estimator_params,
            random_state=random_state,
            warm_start=warm_start,
            max_samples=max_samples,
        )
        BaseEstModel.__init__(
            self,
            is_discrete_outcome=is_discrete_outocme,
            is_discrete_treatment=is_discrete_treatment,
            categories=categories,
        )

        self.verbose = verbose
        self.max_depth = max_depth
        self.min_samples_split = min_samples_split
        self.min_samples_leaf = min_samples_leaf
        self.min_weight_fraction_leaf = min_weight_fraction_leaf
        self.max_features = max_features
        self.max_leaf_nodes = max_leaf_nodes
        self.min_impurity_decrease = min_impurity_decrease

    # TODO: the current implementation is a simple version
    # TODO: add shuffle sample
    def fit(
        self,
        data,
        outcome,
        treatment,
        adjustment=None,
        covariate=None,
    ):
        super().fit(
            data, outcome, treatment, adjustment=adjustment, covariate=covariate
        )

        y, x, w, v = convert2array(data, outcome, treatment, adjustment, covariate)
        for k, value in {"y": y, "x": x, "v": v}.items():
            setattr(self, "_" + k, value)

        if y.ndim == 1:
            y = y.reshape(-1, 1)

        # Determin treatment settings
        if self.categories == "auto" or self.categories is None:
            categories = "auto"
        else:
            categories = list(self.categories)

        if self.is_discrete_treatment:
            self.transformer = OrdinalEncoder(categories=categories)
            self.transformer.fit(x)
            x = self.transformer.transform(x)

        self.n_outputs_ = y.shape[1]

        self._validate_estimator()

        random_state = check_random_state(self.random_state)

        if not self.warm_start or not hasattr(self, "estimators_"):
            # Free allocated memory, if any
            self.estimators_ = []

        n_more_estimators = self.n_estimators - len(self.estimators_)
        y_ = y.squeeze()
        if n_more_estimators < 0:
            raise ValueError(
                f"n_estimators={self.n_estimators} must be larger or equal to "
                f"len(estimators_)={len(self.estimators_)} when warm_start==True"
            )
        else:
            trees = [
                self._make_estimator(append=False, random_state=random_state)
                for i in range(n_more_estimators)
            ]

            # Parallel loop: we prefer the threading backend as the Cython code
            # for fitting the trees is internally releasing the Python GIL
            # making threading more efficient than multiprocessing in
            # that case. However, for joblib 0.12+ we respect any
            # parallel_backend contexts set at a higher level,
            # since correctness does not rely on using threads.
            trees = Parallel(
                n_jobs=self.n_jobs,
                verbose=self.verbose,
                prefer="threads",
            )(delayed(t._fit_with_array)(x, y_, w, v, i) for i, t in enumerate(trees))

            # Collect newly grown trees
            self.estimators_.extend(trees)
        self._is_fitted = True

        return self

    def estimate(self, data=None, **kwargs):
        effect_ = self._prepare4est(data=data)
        return effect_

    def effect_nji(self, *args, **kwargs):
        return super().effect_nji(*args, **kwargs)

    def apply(self):
        pass

    def decision_path(
        self,
    ):
        pass

    def feature_importances_(self):
        pass

    @property
    def n_features_(self):
        pass

    # TODO: support oob related methods

    def _check_features(self, data):
        v = self._v if data is None else convert2array(data, self.covariate)[0]
        return v

    def _prepare4est(self, data=None):
        assert self._is_fitted, "The model is not fitted yet."
        v = self._check_features(data=data)
        alpha = self._compute_alpha(v)
        inv_grad_, theta_ = self._compute_aug(self._y, self._x, alpha)
        theta = np.einsum("njk,nk->nj", inv_grad_, theta_)
        return theta

    def _compute_aug(self, y, x, alpha):
        pass

    def _compute_alpha(self, v):
        # first implement a version which only take one example as its input
        # lock = threading.Lock()
        w = v.copy()
        if self.n_outputs_ > 1:
            raise ValueError(
                "Currently do not support the number of output which is larger than 1"
            )

        alpha_collection = Parallel(n_jobs=self.n_jobs, verbose=self.verbose,)(
            delayed(_prediction)(e._predict_with_array, w, v, self._v)
            for e in self.estimators_
        )
        return np.array(alpha_collection).sum(axis=0) / self.n_estimators
