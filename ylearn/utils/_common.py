import contextlib as _contextlib
import inspect
import warnings
from collections import OrderedDict

import numpy as np
import pandas as pd


class const:
    TASK_AUTO = 'auto'
    TASK_BINARY = 'binary'
    TASK_MULTICLASS = 'multiclass'
    TASK_REGRESSION = 'regression'
    TASK_MULTILABEL = 'multilabel'


def to_df(**data):
    dfs = []
    for k, v in data.items():
        if v is None:
            pass  # ignore the item
        elif len(v.shape) == 1:
            dfs.append(pd.Series(v, name=k))
        elif v.shape[1] == 1:
            dfs.append(pd.DataFrame(v, columns=[k]))
        else:
            dfs.append(pd.DataFrame(v, columns=[f'{k}_{i}' for i in range(v.shape[1])]))
    df = pd.concat(dfs, axis=1)
    return df


@_contextlib.contextmanager
def context(msg):
    try:
        yield
    except Exception as ex:
        if ex.args:
            msg = u'{}: {}'.format(msg, ex.args[0])
        else:
            msg = str(msg)
        ex.args = (msg,) + ex.args[1:]
        raise


def is_notebook():
    """
    code from https://stackoverflow.com/questions/15411967/how-can-i-check-if-code-is-executed-in-the-ipython-notebook
    """
    try:
        shell = get_ipython().__class__.__name__
        if shell == 'ZMQInteractiveShell':
            return True  # Jupyter notebook or qtconsole
        elif shell == 'TerminalInteractiveShell':
            return False  # Terminal running IPython
        else:
            return False  # Other type (?)
    except:
        return False


def view_pydot(pdot, prog='dot'):
    try:
        from IPython.display import Image, display

        img = Image(pdot.create_png(prog=prog))
        display(img)
    except Exception as e:
        warnings.warn(f'Failed to display pydot image: {e}.')


def unique(y):
    if hasattr(y, 'unique'):
        uniques = set(y.unique())
    else:
        uniques = set(y)
    return uniques


def infer_task_type(y, excludes=None):
    assert excludes is None or isinstance(excludes, (list, tuple, set))

    if len(y.shape) > 1 and y.shape[-1] > 1:
        labels = list(range(y.shape[-1]))
        task = const.TASK_MULTILABEL
        return task, labels

    uniques = unique(y)
    if uniques.__contains__(np.nan):
        uniques.remove(np.nan)
    if excludes is not None and len(excludes) > 0:
        uniques -= set(excludes)
    n_unique = len(uniques)
    labels = []

    if n_unique == 2:
        task = const.TASK_BINARY  # TASK_BINARY
        labels = sorted(uniques)
    else:
        if str(y.dtype).find('float') >= 0:
            task = const.TASK_REGRESSION
        else:
            if n_unique > 1000:
                if str(y.dtype).find('int') >= 0:
                    task = const.TASK_REGRESSION
                else:
                    raise ValueError('The number of classes exceeds 1000, please confirm whether '
                                     'your predict target is correct ')
            else:
                task = const.TASK_MULTICLASS
                labels = sorted(uniques)
    return task, labels


def get_params(obj, include_default=False):
    def _get_init_params(cls):
        init = cls.__init__
        if init is object.__init__:
            return []

        init_signature = inspect.signature(init)
        parameters = [p for p in init_signature.parameters.values()
                      if p.name != 'self']  # and p.kind != p.VAR_KEYWORD]
        return parameters

    fn_get_params = getattr(obj, 'get_params', None)
    if callable(fn_get_params):
        return fn_get_params()

    out = OrderedDict()
    for p in _get_init_params(type(obj)):
        name = p.name
        value = getattr(obj, name, None)
        if include_default or value is not p.default:
            out[name] = value

    return out


def to_repr(obj, excludes=None):
    try:
        if excludes is None:
            excludes = []
        out = ['%s=%r' % (k, v) for k, v in get_params(obj).items() if k not in excludes]
        repr_ = ', '.join(out)
        return f'{type(obj).__name__}({repr_})'
    except Exception as e:
        return f'{type(e).__name__}:{e}, at <to_repr>: {type(obj).__name__}'