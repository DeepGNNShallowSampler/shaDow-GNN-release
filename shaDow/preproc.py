from shaDow.minibatch import MinibatchShallowSampler
from shaDow import TRAIN, VALID, TEST, MODE2STR
import numpy as np
from shaDow.utils import adj_norm_sym, adj_norm_rw, coo_scipy2torch
import torch
import sys
from tqdm import tqdm


class PreprocessGraph:
    def __init__(self, arch_gnn, minibatch_preproc, no_pbar):
        self.minibatch = minibatch_preproc
        self.arch_gnn = arch_gnn
        self.is_transductive = minibatch_preproc.adj[TRAIN].size == minibatch_preproc.adj[TEST].size
        self.no_pbar = no_pbar

    def _ppr(self, adj, signal, pbar, num_target, alpha, itr_max):
        """
        Reference to APPNP method
        Here to be consistent with the C++ PPR sampler, our alpha is actually 1-alpha in APPNP
        """
        alpha = 1 - alpha
        # initialize
        H = signal
        Z = H
        for k in range(itr_max):
            Zk = (1 - alpha) * torch.sparse.mm(adj, Z) + alpha * H
            delta_change = torch.linalg.norm(Z - Zk, ord='fro')
            Z = Zk
            if pbar is not None:
                pbar.update(num_target)
            else:
                print(f"Smooth full grpah signal for ITR {k}")
            if delta_change < 0.015:
                break
        return Z
        

    def _smooth_signals_subg(
                self, 
                adj, 
                signal, 
                target, 
                order : int, 
                pbar, 
                type_norm : str, 
                reduction_orders : str, 
                args : dict,
                add_self_edge : bool=True, 
                is_normed : bool=False
            ):
        if not is_normed:
            if type_norm == 'sym':
                adj_norm = adj_norm_sym(adj, add_self_edge=add_self_edge)
            elif type_norm == 'rw':     # NOTE: we haven't supported add_self_edge for rw norm yet.
                adj_norm = adj_norm_rw(adj)
            elif type_norm == 'ppr':
                assert order == 1
                adj_norm = adj_norm_sym(adj, add_self_edge=True)        # see APPNP
            else:
                raise NotImplementedError
            adj_norm = coo_scipy2torch(adj_norm.tocoo()).to(signal.device)
        else:
            adj_norm = adj
        if type_norm == 'ppr':
            signal_converged = self._ppr(adj_norm, signal, pbar, target.size, **args)[target]
            signal_orig = signal[target]
            if reduction_orders in ['cat', 'concat']:
                signal_out = torch.cat([signal_orig, signal_converged], dim=1).to(signal.device)
            elif reduction_orders == 'sum':
                signal_out = signal_orig + signal_converged
            elif reduction_orders == 'last':
                signal_out = signal_converged
        elif type_norm in ['sym', 'rw']:
            signal_order = signal
            if reduction_orders in ['cat', 'concat']:
                F = signal_order.shape[1]
                F_new = F + order * F
                signal_out = torch.zeros(target.size, F_new).to(signal_order.device)
                signal_out[:, :F] = signal_order[target]
                for _k in range(order):
                    signal_order = torch.sparse.mm(adj_norm, signal_order)
                    signal_out[:, (_k + 1) * F : (_k + 2) * F] = signal_order[target]
                    if pbar is not None:
                        pbar.update(target.size)
            elif reduction_orders == 'sum':
                F_new = signal_order.shape[1]
                signal_out = signal_order[target].copy()
                for _k in range(order):
                    signal_order = torch.sparse.mm(adj_norm, signal_order)
                    signal_out += signal_order[target]
                    if pbar is not None:
                        pbar.update(target.size)
            elif reduction_orders == 'last':
                for _k in range(order):
                    signal_order = torch.sparse.mm(adj_norm, signal_order)
                    if pbar is not None:
                        pbar.update(target.size)
                signal_out = signal_order[target]
        return signal_out, adj_norm

    def smooth_signals_fullg(
                self, 
                tag : str, 
                signal, 
                order : int, 
                type_norm : str, 
                reduction_orders : str, 
                args : dict, 
                add_self_edge : bool=True
            ):
        """ SGC / SIGN / APPNP
        mode: full / subgraph
        ops: concat / sum / replace
        signals = self.minibatch.feat_full / self.get_label_raw()
        """
        N, F = signal.shape
        assert reduction_orders in ['cat', 'concat', 'last', 'sum']
        if reduction_orders in ['cat', 'concat']:
            F_new = F + order * F
        else:
            F_new = F
        signal_new = torch.zeros(N, F_new).to(self.minibatch.feat_full.device)
        pbar = None
        if self.minibatch.mode_sample == self.minibatch.FULL and self.minibatch.is_transductive:
            num_nodes_tqdm = self.minibatch.adj[TEST].shape[0] * (order if type_norm != 'ppr' else args['itr_max'])
            if not self.no_pbar:
                pbar = tqdm(total=num_nodes_tqdm, leave=True, file=sys.stdout)
                pbar.set_description(f"Smoothing {tag} for full graph")
            adj_ = self.minibatch.adj[TEST]
            target_ = np.concatenate([self.minibatch.node_set[m] for m in [TRAIN, VALID, TEST]])
            signal_smoothed, _ = self._smooth_signals_subg(adj_, signal, target_, order, 
                                        pbar, type_norm, reduction_orders, args, add_self_edge=False)
            signal_new[target_] = signal_smoothed.to(signal_new.device)
            if pbar is not None:
                pbar.close()
            print((f"Finished smoothing {tag}\tvariance: {signal.var():.4f} to {signal_new.var():.4f}"))
        else:
            for m in [TRAIN, VALID, TEST]:      # for full SGC, return the full subgraph
                self.minibatch.disable_cache(m)
                self.minibatch.epoch_start_reset(0, m)
                num_nodes_tqdm = self.minibatch.node_set[m].size * (order if type_norm != 'ppr' else args['itr_max'])
                if not self.no_pbar:
                    pbar = tqdm(total=num_nodes_tqdm, leave=True, file=sys.stdout)
                    pbar.set_description(f"Smoothing {tag} {MODE2STR[m].upper()}")
                while not self.minibatch.is_end_epoch(m):
                    # TODO: not yet supporting subgraph ensemble in preproc
                    ret = self.minibatch.one_batch(mode=m, ret_raw_idx=True)
                    _adj_sub, _target_sub, _idx_raw_sub = ret['adj_ens'][0], ret['target_ens'][0], ret['idx_raw'][0]
                    _signal_sub = signal[_idx_raw_sub]
                    _idx_writeback = _idx_raw_sub[_target_sub]
                    signal_smoothed, _ = self._smooth_signals_subg(_adj_sub, _signal_sub, _target_sub, order, 
                                                pbar, type_norm, reduction_orders, args, add_self_edge=False)
                    signal_new[_idx_writeback] = signal_smoothed.to(signal_new.device)
                if pbar is not None:
                    pbar.close()
                nodes_updated = self.minibatch.node_set[m]
                self.minibatch.epoch_end_reset(m)
                print((f"Finished smoothing {tag} of {MODE2STR[m].upper()}\t"
                    f"variance: {signal[nodes_updated].var():.4f} to {signal_new[nodes_updated].var():.4f}"))
        print(f"(Order {order}) Full {tag} matrix variance changes from {signal.var():.4f} to {signal_new.var():.4f}")
        return signal_new
        
    def prepare_raw_label(self, type_label : str):
        assert type_label.lower() != 'none'
        num_nodes = self.minibatch.label_full.shape[0]
        if len(self.minibatch.label_full.shape) == 1:
            _label = self.minibatch.label_full
            num_cls = _label[_label == _label].max().item() + 1
            assert _label[_label == _label].min().item() == 0
        else:
            num_cls = self.minibatch.label_full.shape[1]
        feat_label = torch.zeros(num_nodes, num_cls).to(self.minibatch.feat_full.device)
        mode_node_set = [TRAIN] if type_label.lower() != 'all' else [TRAIN, VALID]
        for md in mode_node_set:
            idx_fill = self.minibatch.node_set[md]
            if type(idx_fill) == np.ndarray:
                idx_fill = torch.from_numpy(idx_fill).to(feat_label.device)
            if len(self.minibatch.label_full.shape) == 1:
                feat_label[idx_fill, self.minibatch.label_full[idx_fill].to(torch.int64)] = 1
            else:
                feat_label[idx_fill] = self.minibatch.label_full[idx_fill].float().to(feat_label.device)
        return feat_label

    def diffusion(self):
        """ GDC

        """
        pass

    def preprocess(self):
        is_adj_changed = is_feat_changed = False
        # smooth features
        if self.arch_gnn['feature_smoothen'].lower() != "none":
            type_norm, order, reduction_orders, args = self.f_decode_smoothen_config(self.arch_gnn['feature_smoothen'])
            feat_orig = self.minibatch.feat_full
            feat_full_new = self.smooth_signals_fullg("feats", feat_orig, order, type_norm, reduction_orders, args)
            is_feat_changed = True
        else:
            feat_full_new = self.minibatch.feat_full
        dim_feat_smooth = feat_full_new.shape[1]
        # smooth labels
        if self.arch_gnn['use_label'].lower() != 'none':
            assert self.is_transductive
            label_orig = self.prepare_raw_label(self.arch_gnn['use_label'])     # we only utilize train labels
            if self.arch_gnn['label_smoothen'].lower() != 'none':
                type_norm, order, reduction_orders, args = self.f_decode_smoothen_config(self.arch_gnn['label_smoothen'])
                label_smooth = self.smooth_signals_fullg("labels", label_orig, order, type_norm, reduction_orders, args)
            else:       # i.e., use original TRAIN label as input
                label_smooth = label_orig
            dim_label_smooth = label_smooth.shape[1]
            is_feat_changed = True
        else:
            label_smooth = None
            dim_label_smooth = 0
        if label_smooth is not None:
            feat_full_new = torch.cat([feat_full_new, label_smooth], dim=1)
        # update adj
        if self.arch_gnn['aggr'] == 'gdc':
            is_adj_changed = True
            raise NotImplementedError
        else:
            adjs_new = self.minibatch.adj
        print(f"DIMENSION: SMOOTHED FEAT = {dim_feat_smooth}, SMOOTHED LABEL = {dim_label_smooth}")
        assert dim_feat_smooth + dim_label_smooth == feat_full_new.shape[1]
        return adjs_new, feat_full_new, is_adj_changed, is_feat_changed,\
                dim_feat_smooth, dim_label_smooth

    def f_decode_smoothen_config(self, config_str):
        assert len(config_str.split('-')) >= 3, "[YML]: format of *_smoothen mismatch"
        type_norm = config_str.split('-')[0].lower()
        if type_norm == 'ppr':
            order = 1       # we regard the 2 orders as the original feat and the converged feat
            assert 5 >= len(config_str.split('-')) >= 4
            reduction_orders, k = config_str.split('-')[2:4]
            args = {'alpha': float(k)}
            if len(config_str.split('-')) == 5:
                args['itr_max'] = int(config_str.split('-')[-1])
            else:
                args['itr_max'] = 100
        elif type_norm in ['sym', 'rw']:
            assert len(config_str.split('-')) == 3
            order, reduction_orders = config_str.split('-')[1:]
            order = int(order)
            args = {}
        else:
            raise NotImplementedError
        return type_norm, order, reduction_orders, args
    