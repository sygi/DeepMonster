import inspect
import numpy as np
import theano
import theano.tensor as T

from baselayers import Layer
from convolution import ConvLayer
from simple import FullyConnectedLayer
from normalizations import batch_norm


class ScanLayer(Layer):
    """
        General class for layers that has to deal with scan. This Layer by itself cannot do any application
        on its own. It has to be subclassed by a class that needs to implement a step
        It works in tandem with RecurrentLayer.

        In general, a subclass of ScanLayer will have to subclass ScanLayer and another class.
        The LSTM for exemple applies a dot through time, so it has the effective application
        of a fullyconnected. You want the information of the fullyconnected for shape propagation.
    """
    # TODO: There is a problem with how the arguments are passed to the step function. If there is another
    # norm that requires params and you want to have it optional with batch norm, having the two sets of
    # keyword is going to crash. Workaround: make these things act like decorator?
    @property
    def non_sequences(self):
        """
            This takes care to infer the arguments to give as non seq to the step function.
            The step functions takes as non seq positional arguments (such as the weight
            matrix) and kwargs (such as batch_norm parameters)
        """
        step_arg_list = inspect.getargspec(self.step).args
        param_names = [x for x in self.param_dict.keys()]

        slice_len = len(param_names)
        sublist = []
        for item in step_arg_list :
            if item in param_names:
                sublist += [item]

        non_seq = []
        for param in sublist :
            non_seq += [getattr(self, param)]

        return non_seq


    def step(self):
        """
            Every theano scan has a step!

            *** The arguments in step NEED to have the same name as the parameters, meaning
            if you do a T.dot(z, U) U is linked to self.U at runtime.

            REMINDER: Even if U used in step can be referenced by self.U, because of the
            arcanes of scan, we need to give it as an argument to the step function in
            the list of non_sequences. That's mainly why this whole jibber jabber class is for.
        """
        pass


    def set_scan_namespace(self, sequences, outputs_info=None, non_sequences=None):
        """
            Every theano scan has a namespace :
                - sequences
                - output_infos
                - non_sequences (this is most of the time all the shared theanovar)
        """
        if getattr(self, 'scan_namespace', None) is not None:
            # if any wrapping layer hacked self.step, it probably hacked
            # the non_sequences list. We want these changes to be preserved
            non_sequences = self.scan_namespace['non_sequences']
        elif non_sequences is None :
            non_sequences = self.non_sequences
        if outputs_info is None:
            outputs_info = self.get_outputs_info()
        namespace = {
            'sequences' : sequences,
            'outputs_info' : outputs_info,
            'non_sequences' : non_sequences,
        }
        self.scan_namespace = namespace


    def before_scan(self, *args, **kwargs):
        """
            Do before scan manipulations and populate the scan namespace.

            This is basically the beginning of the fprop and receives as
            input what is being propagated from the layer below.
        """
        pass


    def scan(self):
        rval, updates = theano.scan(
            self.step,
            sequences=self.scan_namespace['sequences'],
            non_sequences=self.scan_namespace['non_sequences'],
            outputs_info=self.scan_namespace['outputs_info'],
            strict=True)
        return rval


    def after_scan(self, scanout):
        """
            Do manipulations after scan
        """
        return scanout


    def unroll_scan(self):
        rval = [self.scan_namespace['outputs_info']]
        for i in range(self.time_size):
            step_inputs = [s[i] for s in self.scan_namespace['sequences']] + \
                           rval[-1] + self.scan_namespace['non_sequences']
            scan_out = list(self.step(*step_inputs))
            rval += [scan_out]
        # rval is a list of each returned tuple at each time step
        # scan returns a tuple of all elements that have been joined on the time axis
        new_rval = []
        #import ipdb; ipdb.set_trace()
        for i in range(len(rval[1])):
            new_rval += [[]]
            for j in range(1, len(rval)):
                new_rval[i] += [rval[j][i]]

        new_new_rval = []
        for i in new_rval:
            new_new_rval += [T.stack(i, axis=0)]

        return tuple(new_new_rval)


    def apply(self, *args, **kwargs):
        """
            Every ScanLayer has the same structure :
                - do things before scan
                - scan
                - do things after scan
        """
        # this is not very clean and could lead to error, but is there a better way to
        # propagate this information into the step function of scan?
        self.deterministic = kwargs.get('deterministic', False)

        self.before_scan(*args, **kwargs)
        if False and self.time_size is not None:
            # if the time_size is specified we can use a for loop, faster++
            # BROKEN, anyway, never noticed a time gain, only a memory blow up
            rval = self.unroll_scan()
        else:
            rval = self.scan()
        out = self.after_scan(rval)

        return out



class ScanLSTM(ScanLayer, FullyConnectedLayer):
    """
        LSTM implemented with the matrix being 4 times the dimensions so it can be sliced
        between the 4 gates.
    """
    def batch_norm_addparams(self):
            self.param_dict.update({
                'x_gammas' : [(4*self.output_dims[0],), 'ones', self.gamma_scale],
                'h_gammas' : [(4*self.output_dims[0],), 'ones', self.gamma_scale],
                'c_gammas' : [self.output_dims, 'ones', self.gamma_scale],
                'c_betas' : [self.output_dims, 'zeros'],
            })


    def param_dict_initialization(self):
        dict_of_init = {
            'U' : [(4*self.output_dims[0], self.input_dims[0]), 'orth', 0.1],
            'xh_betas' : [(4*self.output_dims[0],), 'zeros'],
            'h0' : [(self.output_dims[0],), 'zeros'],
            'c0' : [(self.output_dims[0],), 'zeros'],
        }
        self.param_dict = dict_of_init


    def initialize(self):
        super(ScanLSTM, self).initialize()
        ### Forget biais init
        forget_biais = self.xh_betas.get_value()
        forget_biais[self.output_dims[0]:2*self.output_dims[0]] = 1.
        self.xh_betas.set_value(forget_biais)


    def op(self, h, U):
        preact = T.dot(h.flatten(2), U.flatten(2).dimshuffle(1,0))
        return preact


    def step(self, x_,
             h_, c_,
             U, xh_betas,
             x_gammas=None, h_gammas=None,
             c_gammas=None, c_betas=None):
        deterministic = self.deterministic
        if x_.ndim == 4 and h_.ndim == 2:
            x_ = x_.flatten(2)

        def _slice(_x, n, dim):
            if _x.ndim == 4:
                return _x[:, n*dim:(n+1)*dim, :, :]
            elif _x.ndim == 3:
                return _x[n*dim:(n+1)*dim, :, :]
            elif _x.ndim == 2:
                return _x[:,n*dim:(n+1)*dim]

        #from theano.tests.breakpoint import PdbBreakpoint
        #bp = PdbBreakpoint('test')
        #dummy_h_, U, x_ = bp(1, dummy_h_, U, x_)

        preact = self.op(h_, U)

        if self.batch_norm :
            x_normal = self.bn(x_, xh_betas, x_gammas, '_x', deterministic)
            h_normal = self.bn(preact, 0, h_gammas, '_h', deterministic)
            preact = x_normal + h_normal
        else :
            xh_betas = xh_betas.dimshuffle(*('x',0) + ('x',) * (preact.ndim-2))
            preact = x_ + preact
            preact = preact + xh_betas

        i = T.nnet.sigmoid(_slice(preact, 0, self.output_dims[0]))
        f = T.nnet.sigmoid(_slice(preact, 1, self.output_dims[0]))
        o = T.nnet.sigmoid(_slice(preact, 2, self.output_dims[0]))
        g = T.tanh(_slice(preact, 3, self.output_dims[0]))

        delta_c_ = i * g

        c = f * c_ + delta_c_

        if self.batch_norm :
            c_normal = self.bn(c, c_betas, c_gammas, '_c', deterministic)
            h = o * T.tanh(c_normal)
        else :
            h = o * T.tanh(c)

        return h, c

    def get_outputs_info(self, n):
        outputs_info = [T.repeat(self.h0[None,...], n, axis=0),
                        T.repeat(self.c0[None,...], n, axis=0)]
        return outputs_info


    def before_scan(self, x, axis=1):
        n_sample = x.shape[axis]
        sequences = [x]
        outputs_info = self.get_outputs_info(n_sample)
        self.set_scan_namespace(sequences, outputs_info)


    def after_scan(self, scanout):
        return scanout[0]



class ScanConvLSTM(ScanLSTM, ConvLayer):
    def __init__(self, filter_size, num_filters, **kwargs):
        # strides is (1,1)
        ConvLayer.__init__(self, filter_size, num_filters, **kwargs)
        self.padding = 'half'


    def batch_norm_addparams(self):
            self.param_dict.update({
                'x_gammas' : [(4*self.num_filters,), 'ones', self.gamma_scale],
                'h_gammas' : [(4*self.num_filters,), 'ones', self.gamma_scale],
                'c_gammas' : [(self.num_filters,), 'ones', self.gamma_scale],
                'c_betas' : [(self.num_filters,), 'zeros'],
            })


    def param_dict_initialization(self):
        dict_of_init = {
            'U' : [(self.num_filters*4, self.num_filters)+self.filter_size,
                   'orth', 0.1],
            'xh_betas' : [(4*self.num_filters,), 'zeros'],
            'h0' : [(self.num_filters,) + self.feature_size, 'zeros'],
            'c0' : [(self.num_filters,) + self.feature_size, 'zeros'],
        }
        self.param_dict = dict_of_init


    def op(self, h, U):
        if self.filter_size == (1,1) :
            preact = T.dot(h.flatten(2), U.flatten(2).dimshuffle(1,0))[:,:,None,None]
        else :
            preact = T.nnet.conv2d(h, U, border_mode='half')
        return preact


    # FIX THAT if needed
"""
    def apply_zoneout(self, step):
_
            Act kind of like a decorator around the step function
            of scan which will perform the zoneout.

            ***ASSUMPTIONS :
                - mask_h is the first element of the sequences list
                - mask_c is the second element of the sequences list
                - h_ is how previous hidden state is named in step signature
                - c_ is how previous cell state is named in step signature

        def zonedout_step(*args, **kwargs):
            mask_h = args[0]
            mask_c = args[1]
            # arglist is unaware of the mask as it is not in the
            # step function signature (thats why the +2)
            arglist = inspect.getargspec(step).args
            h_ = args[arglist.index('h_')+2]
            c_ = args[arglist.index('c_')+2]
            zoneout_flag = args[arglist.index('zoneout_flag')+2]

            args = args[2:]
            h, c = step(*args, **kwargs)

            zoneout_mask_h = T.switch(
                zoneout_flag,
                mask_h,
                T.ones_like(mask_h) * self.zoneout/10.)

            zoneout_mask_c = T.switch(
                zoneout_flag,
                mask_c,
                T.ones_like(mask_c) * self.zoneout)

            zonedout_h = h_ * zoneout_mask_h + h * (T.ones_like(zoneout_mask_h) - zoneout_mask_h)
            zonedout_c = c_ * zoneout_mask_c + c * (T.ones_like(zoneout_mask_c) - zoneout_mask_c)
            return zonedout_h, zonedout_c
        return zonedout_step
"""
