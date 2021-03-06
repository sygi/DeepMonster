import copy
import cPickle as pkl
import numpy as np
import os, socket, time
import theano
import theano.tensor as T

from collections import OrderedDict
from scipy.misc import imsave

from blocks.extensions import SimpleExtension
from blocks.graph import ComputationGraph
from blocks.utils import dict_subset


class Experiment(SimpleExtension):
    """
        This class is intended to do all the savings and bookeeping required
    """
    def __init__(self, name, local_path, network_path, extra_infos='',
                 crush_old=False, full_dump=False, **kwargs):
        kwargs.setdefault('before_first_epoch', True)
        super(Experiment, self).__init__(**kwargs)

        # global full_dump param for all ext if not individually set
        self.full_dump = -1 if full_dump is False else full_dump
        assert crush_old in [False, 'local', 'network', 'all']
        print "Setting up experiment files"

        lt = time.localtime()
        timefootprint = str(lt.tm_year) + str(lt.tm_mon) + str(lt.tm_mday) + \
                str(lt.tm_hour) + str(lt.tm_min)

        local_path += name + '/'
        network_path += name + '/'

        cmd = "mkdir --parents " + local_path
        print "Doing:", cmd
        os.system(cmd)
        cmd = "mkdir --parents " + network_path
        print "Doing:", cmd
        os.system(cmd)

        if len(os.listdir(local_path)) > 0:
            print "Files already in", local_path
        if len(os.listdir(network_path)) > 0:
            print "Files already in", network_path
        if crush_old is not False:
            print "WARNING: Will remove them in 10s (crush_old={})".format(crush_old)
            time.sleep(10) # give time to the user to react
            if crush_old in ['local','all']:
                cmd = 'rm -r {}*'.format(local_path)
                print "Doing:", cmd
                os.system(cmd)
            if crush_old in ['network','all']:
                cmd = 'rm -r {}*'.format(network_path)
                print "Doing:", cmd
                os.system(cmd)

        host = socket.gethostname()

        f = open(network_path + '{}.txt'.format(name), 'w')
        f.write('This experiment named {} has local files on {}\n'.format(name, host))
        f.write(timefootprint+'\n')
        f.write('\n')
        f.write(extra_infos)
        f.write('\n')

        self.exp_name = name
        self.local_path = local_path
        self.network_path = network_path
        self.host = host


    @property
    def epoch(self):
        return self.main_loop.status['epochs_done']


    def do(self, which_callback, *args):
        pass


    def save(self, obj, name, ext, append_time=False, network_move=False):
        assert ext in ['npz', 'pkl', 'png']
        name = self.exp_name + '_' + name
        if append_time:
            name += str(self.epoch)
        name += '.' + ext
        tmp = self.local_path + 'tmp' + name
        target = self.local_path + name

        if ext == 'npz':
            np.savez(open(tmp, 'w'), obj)
        elif ext == 'pkl':
            pkl.dump(obj, open(tmp, 'w'))
        elif ext == 'png':
            # this is more fancy stuff. obj is expected to be a np array at this point
            img = prepare_png(obj)
            imsave(tmp, img)
            pass

        cmd = 'mv {} {}'.format(tmp, target)
        print "Doing:", cmd
        os.system(cmd)

        if network_move:
            t = 0
            while t<10:
                try:
                    nettarget = self.network_path + name
                    cmd = 'cp {} {}'.format(target, nettarget)
                    print "Doing:", cmd
                    os.system(cmd)
                    return
                except IOError as err:
                    print "Error writing, will retry {} times".format(10-t)
                t += 1
                time.sleep(t)
            raise err



class FileHandlingExt(SimpleExtension):
    """
        This extension is made to interact with the experiment extension.
        Any subclass of this one will dump its files to the experiment one
        for savings

        full_dump := freq at which a full dump will be made. A full dump
        should be a more extensive way of savings files and usually implies a move
        of those files to the servers.

        file_format := a particular file_format a child extension would
        like to use. Ex.: SaveExperiment won't care, but Sample will.
    """
    def __init__(self, full_dump=None, file_format='png', **kwargs):
        super(FileHandlingExt, self).__init__(**kwargs)
        self.file_format = file_format
        self._full_dump = full_dump


    def do(self, which_callback, *args) :
        if not hasattr(self, 'exp_obj'):
            self.exp_obj = self.main_loop.find_extension('Experiment')
            if self._full_dump is None:
                self.full_dump = self.exp_obj.full_dump
            else:
                self.full_dump = -1 if self._full_dump is False else self._full_dump

        self.epoch = self.main_loop.status['epochs_done']
        if self.full_dump != -1 and self.epoch % self.full_dump == 0:
            self._do_full_dump()
        else:
            self._do()


    def _do(self):
        pass


    def _do_full_dump(self):
        # if full dump is not implemented, by default dodo with network_move
        self._do(network_move=True)



class SaveExperiment(FileHandlingExt):
    def __init__(self, parameters, save_optimizer=True,
                 on_best=False, **kwargs) :
        super(SaveExperiment, self).__init__(**kwargs)

        self.parameters = parameters
        self.save_optimizer = save_optimizer
        self.on_best = on_best
        self.best_val = np.inf


    def _save_parameters(self, prefix='', network_move=False):
        print "Saving Parameters..."
        model_params = OrderedDict()
        for param in self.parameters :
            model_params.update(
                {param.name : param.get_value()})

        self.exp_obj.save(model_params, prefix + 'parameters', 'pkl', network_move=network_move)


    def _save_optimizer(self, prefix='', network_move=False):
        print "Saving Optimizer..."
        # We have to save loop on the attributes of the step_rule
        optimizer_params = OrderedDict()
        for update_pair in self.main_loop.algorithm.updates:
            if update_pair[0].name is None:
                continue
            name = update_pair[0].name
            if 'OPT' in name:
                optimizer_params.update(
                    {name : update_pair[0].get_value()})

        self.exp_obj.save(optimizer_params, prefix + 'optimizer', 'pkl', network_move=network_move)


    def _do(self):
        do_save = True
        # if on_best is not False, it should be a string pointing to something
        # reachable in the main loop like 'valid_missclass'
        if self.on_best is not False :
            epoch = self.main_loop.status['_epoch_ends'][-1]
            value = self.main_loop.log[epoch][self.on_best]
            if value > self.best_val:
                do_save = False
            else:
                self.best_val = value

        prefix = '' if self.on_best is False else 'best_'
        if do_save:
            self._save_parameters(prefix)

        if do_save and self.save_optimizer:
            self._save_optimizer(prefix)


    def _do_full_dump(self):
        print "Time for a full dump..."
        # this save implies a full dump of the training status (not only on a best one)
        # and with the main_loop infos so everything can be resumed properly
        self._save_parameters(network_move=True)
        self._save_optimizer(network_move=True)
        #pkl.dump(getattr(self.main_loop, 'status'), open(self.path + '_main_loop_status.pkl', 'w'))
        #pkl.dump(getattr(self.main_loop, 'log'), open(self.path + '_main_loop_log.pkl', 'w'))
        self.exp_obj.save(getattr(self.main_loop, 'log'), 'main_loop_log', 'pkl', network_move=True)



class LoadExperiment(FileHandlingExt):
    def __init__(self, parameters, load_optimizer=True, full_load=False, which_load='local', **kwargs) :
        kwargs.setdefault('before_first_epoch', True)
        super(LoadExperiment, self).__init__(**kwargs)

        self.parameters = parameters
        self.path = self.exp_obj.local_path if which_load is 'local' else self.exp_obj.network_path
        self.load_optimizer = load_optimizer
        self.full_load = full_load


    def do(self, which_callback, *args) :
        # this extension need to be used in case of requeue so if for first time launch, not crash
        if not os.path.isfile(self.path+'_parameters.pkl'):
            print "No file found, no loading"
            return
        if self.full_load :
            print "Full load activated..."
            # a full load loads: main_loop.status, main_loop.log, parameters.pkl and optimizer.pkl
            # it will also hack through other extensions to set them straight
            if not self.load_optimizer:
                print "WARNING: You asked for a full load but load_optimizer is at False"
            ml_log = pkl.load(open(self.path+'_main_loop_log.pkl', 'r'))
            setattr(self.main_loop, 'log', ml_log)

            # if saveparameters had 'on_best' it needs to keep track of the good best valid
            for ext in self.main_loop.extensions:
                if getattr(ext, 'on_best', False) is not False:
                    for log in self.main_loop.log.values():
                        if len(log) == 0 :
                            # there is a lot of crappy {} in the main loop
                            continue
                        val = log[ext.on_best]
                        if val < ext.best_val:
                            ext.best_val = val

        load_parameters(self.path + '_parameters.pkl', self.parameters)

        if self.load_optimizer:
            print "Loading Optimizer at", self.path+'_optimizer.pkl'
            optimizer_params = pkl.load(open(self.path+'_optimizer.pkl', 'r'))
            update_pair_list = self.main_loop.algorithm.updates
            for param in optimizer_params.keys():
                param_was_assigned = False
                for update_pair in update_pair_list:
                    if update_pair[0].name is None:
                        continue
                    name = update_pair[0].name
                    if param == name:
                        update_pair[0].set_value(optimizer_params[param])
                        param_was_assigned = True
                if not param_was_assigned:
                    print "WARNING: parameter "+attr_name+" of loaded optimizer unassigned!"



def load_parameters(path, parameters) :
    print "Loading Parameters at", path
    saved_parameters = pkl.load(open(path, 'r'))
    for sparam in saved_parameters.keys() :
        param_was_assigned = False
        for param in parameters:
            if param.name == sparam:
                param.set_value(saved_parameters[sparam])
                param_was_assigned = True
                break
        if not param_was_assigned :
            print "WARNING: parameter "+param+" of loaded parameters unassigned!"



class LogAndSaveStuff(FileHandlingExt):
    def __init__(self, stuff_to_save, suffix='monitored', **kwargs) :
        self.nan_guard = kwargs.pop('nan_guard', False)
        super(LogAndSaveStuff, self).__init__(**kwargs)

        # this should be a list of strings of variables names to be saved
        # so we can follow their evolution over epochs
        # all the vars that we are trying to save should be numpy arrays!!
        self.stuff_to_save = stuff_to_save
        self.suffix = suffix


    def _do(self, network_move=False) :
        #import ipdb ; ipdb.set_trace()
        # the log wont be done yet
        epoch = self.main_loop.status['_epoch_ends'][-1]

        if len(self.main_loop.status['_epoch_ends']) == 1 :
            dictofstuff = OrderedDict()

            for stuff in self.stuff_to_save :
                dictofstuff.update(
                    {stuff : self.main_loop.log[epoch][stuff]})

        else :
            path = self.exp_obj.local_path + self.exp_obj.exp_name + '_monitored.pkl'
            f = open(path, 'r')
            dictofstuff = pkl.load(f)
            f.close()

            for stuff in self.stuff_to_save :
                if 'accuracy' in stuff :
                    if self.nan_guard and np.isnan(self.main_loop.log[epoch][stuff]):
                        print "ERROR: NAN detected!"
                        import ipdb ; ipdb.set_trace()
                oldnumpy_stuff = dictofstuff[stuff]
                newnumpy_stuff = np.append(oldnumpy_stuff,
                                           self.main_loop.log[epoch][stuff])

                dictofstuff[stuff] = newnumpy_stuff

        self.exp_obj.save(dictofstuff, self.suffix, 'pkl', network_move=network_move)



class Sample(FileHandlingExt):
    def __init__(self, model, **kwargs) :
        super(Sample, self).__init__(**kwargs)
        self.model = model


    def _do(self, network_move=False) :
        print "Sampling..."
        samples = self.model.sampling_function()
        self.exp_obj.save(samples, 'samples', self.file_format, append_time=True,
                          network_move=network_move)



class Reconstruct(FileHandlingExt):
    def __init__(self, model, datastream, **kwargs) :
        super(Reconstruct, self).__init__(**kwargs)
        self.model = model
        self.datastream = datastream #fuel object


    def _do(self, network_move=False) :
        print "Reconstructing..."

        data = next(self.datastream.get_epoch_iterator())
        x, reconstructions = self.model.reconstruction_function(*data)
        self._do_save(x, reconstructions, network_move)


    # the do is getting deeper...
    def _do_save(self, x, reconstructions, network_move=False):
        if self.file_format == 'npz':
            out = np.concatenate((x[np.newaxis], reconstructions[np.newaxis]), axis=0)
            self.exp_obj.save(out, 'reconstructions', 'npz',
                              append_time=True, network_move=network_move)
        else:
            self.exp_obj.save(x, 'src_rec', 'png', append_time=True,
                              network_move=network_move)
            self.exp_obj.save(reconstructions, 'reconstructions', 'png', append_time=True,
                              network_move=network_move)



class FancyReconstruct(Reconstruct):
    def __init__(self, model, datastream, nb_class, **kwargs) :
        super(FancyReconstruct, self).__init__(model, None, **kwargs)

        if not isinstance(datastream, list):
            datastream = [datastream]
        k = 10/len(datastream)
        self.nb_class = nb_class
        data = []

        # build up a dataset of 10 examples per class (not too memory hungry?)
        for j in range(len(datastream)):
            epitr = datastream[j].get_epoch_iterator()
            shape = next(epitr)[0].shape[-3:]
            _data = np.empty((k,nb_class,)+shape, dtype=np.float32)
            cl_accumulated = np.zeros(nb_class)

            for batches in epitr:
                targets = batches[1].flatten()
                for i, target in enumerate(targets):
                    if cl_accumulated[target] < j*k + k:
                        _data[cl_accumulated[target]%k,target,...] = batches[0][i]
                        cl_accumulated[target] += 1
            data += [_data]

        assert cl_accumulated.sum() == nb_class * 10
        self.data = np.stack(data, axis=0)



    def _do(self, network_move=False):
        print "Reconstructing..."
        data = self.data.reshape((10*self.nb_class,)+self.data.shape[-3:])
        x, reconstructions = self.model.reconstruction_function(data)
        self._do_save(x, reconstructions, network_move)



class FrameGen(FileHandlingExt):
    def __init__(self, model, datastream, **kwargs) :
        super(FrameGen, self).__init__(**kwargs)

        self.model = model
        self.datastream = datastream #fuel object
        self.epitr = datastream.get_epoch_iterator()


    def _do(self, network_move=False):
        print "Frame Generation..."

        # reset the epoch iterator every now and then
        if self.count % 10 == 0:
            self.epitr = self.datastream.get_epoch_iterator()
        samples = self.model.sampling_function(next(self.epitr))

        self.exp_obj.save(samples, 'samples', 'npz', append_time=True,
                          network_move=network_move)



class AdjustSharedVariable(SimpleExtension):
    def __init__(self, shared_dict, **kwargs):
        super(AdjustSharedVariable, self).__init__(**kwargs)
        # shared_dict is a dictionnary with the following mapping :
        # {theano.shared : f(t, x)}
        # f(t, x) represent the new value of the shared in function of epoch and current val
        self.shared_dict = shared_dict


    def do(self, which_callback, *args) :
        for shared, func in self.shared_dict.iteritems() :
            current_val = shared.get_value()
            epoch = self.main_loop.status['epochs_done']
            shared.set_value(func(epoch, current_val))



# borrowed from Kyle
def prepare_png(X):
    def color_grid_vis(X):
        ngrid = int(np.ceil(np.sqrt(len(X))))
        npxs = np.sqrt(X[0].size/3)
        img = np.zeros((npxs * ngrid + ngrid - 1,
                        npxs * ngrid + ngrid - 1, 3))
        for i, x in enumerate(X):
            j = i % ngrid
            i = i / ngrid
            x = tf(x)
            img[i*npxs+i:(i*npxs)+npxs+i, j*npxs+j:(j*npxs)+npxs+j] = x
        return img

    def bw_grid_vis(X):
        ngrid = int(np.ceil(np.sqrt(len(X))))
        npxs = np.sqrt(X[0].size)
        img = np.zeros((npxs * ngrid + ngrid - 1,
                        npxs * ngrid + ngrid - 1))
        for i, x in enumerate(X):
            j = i % ngrid
            i = i / ngrid
            x = tf(x)
            img[i*npxs+i:(i*npxs)+npxs+i, j*npxs+j:(j*npxs)+npxs+j] = x
        return img

    def tf(x):
        if x.min() < -0.25:
            x = (x + 1.) / 2.
        return x.transpose(1, 2, 0)

    if X.shape[-3] == 3:
        return color_grid_vis(X)
    elif X.shape[-3] == 1:
        return bw_grid_vis(X)
    else:
        raise ValueError("What the hell is this channel shape?")



class SwitchModelType(SimpleExtension):
    # This extention should be placed first in the list and will
    # take care of the switched between a training model and inference model
    def __init__(self, model, inputs=None, outputs=None, data_stream=None, n_batch=None, **kwargs):
        kwargs.setdefault('after_epoch', True)
        kwargs.setdefault('before_epoch', True)
        super(SwitchModelType, self).__init__(**kwargs)
        self.model = model
        self.inputs = inputs
        self.input_names = [v.name for v in inputs]
        self.data_stream = data_stream

        if data_stream is not None:
            for part in self.model.model_parts:
                part.modify_bnflag('n_batch', n_batch)

            graph = ComputationGraph(outputs)
            bn_mean = [v for v in graph.variables if v.name is not None and 'bn_mean' in v.name]
            bn_popmu = [v for v in graph.shared_variables if v.name is not None and 'pop_mu' in v.name]
            self.bn_popmu = bn_popmu
            updates = []

            for pop_mu in bn_popmu:
                this_mean = None
                name = pop_mu.name.split('_pop_mu')[0]
                for mean in bn_mean :
                    if name in mean.name:
                        this_mean = mean
                        break
                if this_mean is not None:
                    #raise ValueError('Failed to connect updates for batchnorm inference')
                    updates += [(pop_mu, this_mean)]

            print updates
            self.compute_pop_stats = theano.function(
                inputs, [], updates=updates, on_unused_input='ignore')


    def do(self, which_callback, *args) :
        if which_callback == 'before_epoch':
            self.model.switch_for_training()
        elif which_callback == 'after_epoch':
            self.model.switch_for_inference()

            if self.data_stream is not None:
                for pop_mu in self.bn_popmu:
                    shape = pop_mu.get_value().shape
                    pop_mu.set_value(np.zeros(shape).astype(np.float32))

                for batch in self.data_stream.get_epoch_iterator(as_dict=True):
                    batch = dict_subset(batch, self.input_names)
                    self.compute_pop_stats(**batch)

                for part in self.model.model_parts:
                    part.modify_bnflag('bn_flag', 0.)
