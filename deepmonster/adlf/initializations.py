import numpy as np
from rali.utils import rng_np


def norm_weight_tensor(shape):
    return np.random.normal(size=shape).astype('float32')


def orthogonal_weight_tensor(shape):
    """
    Random orthogonal matrix as done in blocks
    Orthogonal() for a 2D or 4D tensor.
    4D case : It will return an array of orthogonal matrices
    """
    if len(shape) == 2 :
        if shape[0] == shape[1] :
            M = rng_np.randn(*shape).astype(np.float32)
            Q, R = np.linalg.qr(M)
            Q = Q * np.sign(np.diag(R))
            return Q
        elif shape[1] % shape[0] == 0:
            print "WARNING: You asked for a orth initialization for a 2D tensor"+\
                    " that is not square, but it seems possible to make it orth by blocks"
            weight_tensor = np.empty(shape, dtype=np.float32)
            blocks_of_orth = shape[1] // shape[0]
            for i in range(blocks_of_orth):
                M = rng_np.randn(shape[0],shape[0]).astype(np.float32)
                Q, R = np.linalg.qr(M)
                Q = Q * np.sign(np.diag(R))
                weight_tensor[:,i*shape[0]:(i+1)*shape[0]] = Q
            return weight_tensor
        else :
            print "WARNING: You asked for a orth initialization for a 2D tensor"+\
                    " that is not square and not square by block. Falling back to norm"
            return norm_weight_tensor(shape)

    elif len(shape) == 3 :
        print "WARNING: You asked for a orth initialization for 3D tensor"+\
                " it is not implemented. Falling back to norm init."
        return norm_weight_tensor(shape)

    assert shape[2] == shape[3]
    if shape[2] == 1 :
        return norm_weight_tensor(shape)

    weight_tensor = np.empty(shape, dtype=np.float32)
    shape_ = shape[2:]

    for i in range(shape[0]):
        for j in range(shape[1]) :
            M = rng_np.randn(*shape_).astype(np.float32)
            Q, R = np.linalg.qr(M)
            Q = Q * np.sign(np.diag(R))
            weight_tensor[i,j,:,:] = Q

    return weight_tensor


def ones_tensor(shape):
    return np.ones(shape).astype(np.float32)


def zeros_tensor(shape):
    return np.zeros(shape).astype(np.float32)


def identity_tensor(shape):
    assert shape[0] == shape[1]
    return np.identity(shape[0], dtype=np.float32)


initialization_method = {
    'norm' : norm_weight_tensor,
    'orth' : orthogonal_weight_tensor,
    'ones' : ones_tensor,
    'zeros' : zeros_tensor,
    'identity' : identity_tensor,
}


class Initialization(object):
    """
        The goal of the Initilization object is to support different
        initilization scheme for different variables when constructing
        a layer object. It should support legacy way of doing it
    """
    def __init__(self, vardict):
        self.vardict = vardict
        self.initialization_method = initialization_method


    def has_var(self, varname):
        """
            The init scheme will query this object to see if a var is here.
            If it isnt, it will fall back on its default initialization specified
            in the Layer.dict_init_initialization
        """
        return self.vardict.has_key(varname)


    def get_init_tensor(self, varname, shape):
        return self.vardict[varname](shape)


    def get_old_init_method(self, initmethodname, shape, scale=1.):
        """
            Legacy / default compatibility
        """
        return self.initialization_method[initmethodname](shape) * scale


### The objects below are wrapper for one particular init method

class Gaussian(object):
    def __init__(self, mu=0., std=1.):
        self.mu = mu
        self.std = std

    def __call__(self, shape):
        return self.mu + self.std * np.random.randn(*shape).astype(np.float32)


class GaussianHe(object):
    def __init__(self, axis=0, coeff=0.):
        self.axis = axis
        self.coeff = coeff

    def __call__(self, shape):
        wt = np.random.normal(size=shape).astype('float32')
        return wt * np.sqrt(2. / ((1. + self.coeff**2) * shape[self.axis]))


class ScalableInit(object):
    def __init__(self, scale=1.):
        self.scale = scale

class Constant(ScalableInit):
    def __call__(self, shape):
        return np.ones(shape).astype(np.float32) * self.scale

class Orthogonal(ScalableInit):
    def __call__(self, shape):
        return orthogonal_weight_tensor(shape) * self.scale

class IdentityMatrix(ScalableInit):
    def __init__(self, scale=1., onwhichtuple=(0,1)):
        super(IdentityMatrix, self).__init__(scale)
        self.onwhichtuple = onwhichtuple

    def __call__(self, shape):
        if len(shape) > 2:
            raise NotImplementedError
        assert shape[0] == shape[1]
        matrix = np.identity(shape[0], dtype=np.float32)
        return matrix



if __name__ == "__main__":
    from mlp import FullyConnectedLayer
    from convolution import ConvOnSequence
    fl = FullyConnectedLayer(input_size=20, output_size=30, prefix='fl',
                             weight_norm=True, train_g=True)
    fl.initialize()
    conv = ConvOnSequence(3, num_channels=10, num_filters=20,
                          mem_size_x=5, mem_size_y=5, prefix='conv',
                          weight_norm=True, train_g=True)
    conv.initialize()
    import ipdb; ipdb.set_trace()