# Deep GNN, Shallow Sampling

Anonymous GitHub link: https://github.com/DeepGNNShallowSampler/shaDow-GNN-release

## Overview

This repository contains the code for all the experiments in our ICML submission. 

We have extended shaDow-GNN into a general and powerful learning pipeline for graph representation learning. The training of shaDow-GNN can be abstracted as three major steps: 

### Preprocessing

The preprocessing steps may
* Smooth the node features
* Augment the node features with training labels. 

The first point is similar to what SGC and SIGN did (it's just we convert the original algorithm into the shaDow version). The second point is inspired by the methods on the OGB leaderboard (only applicable under the transductive setting). 

Note that preprocessing is turned off when performing the baseline comparisons in Table 1 (for fairness), and when training the papers100M graph (we don't have a machine with huge RAM to accommodate the additional features of the 100M nodes..). 

### Training

shaDow-GNN now supports six different backbone architectures, including: *GCN*, *GraphSAGE*, *GAT*, *GIN*, *JK-Net* and *SGC*. 

In addition, as mentioned in Section 3.3, we support the following architectural extensions:

* Samplers: *k-hop*, *PPR*, and the *ensemble* of them
* Subgraph pooling / READOUT: *CenterPooling*, *SortPooling*, *MaxPooling*, *MeanPooling*, *SumPooling*

### Postprocessing

After the training is finished, we can reload the stored checkpoint to perform the following post-processing steps:
* *C&S* (transductive only): we borrow the DGL implementation of C&S to perform smoothening of the predictions generated by shaDow-GNN.
* *Ensemble*: Ensemble can be done either in an "end-to-end" fashion during the above training step, or as a postprocessing step. In the paper, our discussion is based on the "end-to-end" ensemble. 

## Hardware requirements

Due to its flexibility in minibatching, shaDow-GNN requires the minimum hardware for training and inference computation. 
Most of our experiments can be run on a desktop machine. Even the largest graph of 111 million nodes can be trained on a low-end server. 

The main computation operations include:
* Subgraph sampling, where we construct a local subgraph for each target node independently. This part is parallelized on CPU by C++ and OpenMP. 
* Forward / backward propagation of the GNN model. This part is parallelized on GPU via PyTorch. 

We summarize the recommended *minimum* hardware spec for the three OGB graphs:

| Graph | Num. nodes | CPU cores | CPU RAM | GPU memory |
|:-----:|:----------:|:---------:|:-------:|:----------:|
| ogbn-arxiv | 0.2M | 4 | 8GB | 4GB |
| ogbn-products | 2.4M | 4 | 32GB | 4GB |
| ogbn-papers100M | 111.1M | 4 | 128GB | 4GB |

If you have more powerful machines, you can simply scale up the performance by increasing the batch size. In our experiments, we have tested on GPUs ranging from NVIDIA GeForce GTX 1650 to NVIDIA GeForce RTX 3090. 


## Data format

When you run shaDow-GNN for the first time, we will convert the graph data from the OGB or GraphSAINT format into the shaDow-GNN format. 
The converted data files are (by default) stored in the `./data/<graph_name>` directory. 

**NOTE**: the initial data conversion may take a while for large graphs (e.g., for papers100M). Please be patient. 

### General shaDow format

We briefly describe the shaDow data format. You should not need to worry about the details unless you want to prepare your own dataset. Each graph is defined by the following files: 
* `adj_full_raw.npz` / `adj_full_raw.npy`: The adjacency matrix of the full graph (consisting of all the train / valid / test nodes). It can either be a `*.npz` file of type `scipy.sparse.csr_matrix`, or a `*.npy` file containing the dictionary `{'indptr': numpy.ndarray, 'indices': numpy.ndarray, 'data': numpy.ndarray}`. 
* `adj_train_raw.npz` / `adj_train_raw.npy`: The adjacency matrix induced by all training nodes (ONLY used in inductive learning). 
* `label_full.npy`: The `numpy.ndarray` representing the labels of all the train / valid / test nodes. If this matrix is 2D, then a row is a one-hot encoding of the label(s) of a node. If this is 1D, then an element is the label index of a node. In any case, the first dimension equals the total number of nodes. 
* `feat_full.npy`: The `numpy.ndarray` representing the node features. The first dimension of the matrix equals the total number of nodes. 
* `split.npy`: The file stores a dictionary representing the train / valid / test splitting. The keys are train / valid / test. The values are `numpy` array of the node indices for the corresponding split. 
* (Optional) `adj_full_undirected.npy`: This is a cache file storing the graph after converting `adj_full_raw` into undirected (e.g., the raw graph of `ogbn-arxiv` is directed). 
* (Optional) `adj_train_undirected.npy`: Similar as above. Converted from `adj_train_raw` into undirected. 
* (Optional) `cpp/adj_<full|train>_<indices|indptr|data>.bin`: These are the cache files for the C++ sampler. We store the corresponding `*.npy` / `*.npz` files as binary files so that the C++ sampler can directly load the graph without going through the layer of PyBind11 (see below). For gigantic graphs such as `ogbn-papers100M`, the conversion from `numpy.ndarray` to C++ `vector` seems to be slow (maybe an issue of PyBind11). 
* (Optional) `ppr_float/<neighs|scores>_<transductive|inductive>_<ppr params>.bin`: These are the cache files for the C++ PPR sampler. We store the PPR values and node indices for the close neighbors of each target as the external binary files. Therefore, we do not need to run PPR multiple times when we perform parameter tuning (even through running PPR from scratch is still much cheaper than the model training). 

### Graphs used in the paper

To train shaDow-GNN on the six graphs used in the paper:
* For the three OGB graphs (i.e., `ogbn-arxiv`, `ogbn-products`, `ogbn-papers100M`), you don't need to manually download anything. Just execute the training command (see below). 
* For the three other graphs (i.e., `Flickr`, `Reddit`, `Yelp`), these are listed in the officially GraphSAINT repo. Please manually download from the [link provided by GraphSAINT](https://drive.google.com/open?id=1zycmmDES39zVlbVCYs88JTJ1Wm5FbfLz), and place all the downloaded files under the `./data/saint/<graph name>/` directory.
    * E.g., for `Flickr`, the directory should look something like (note the **lower case** for graph name)

```
data/
└───saint/
    └───flickr/
        └───adj_full.npz
            class_map.json
            ...
```

The script for converting from OGB / SAINT into shaDow format is `./shaDow/data_converter.py`. 

## Build and Run

**Step 0**: Make sure you create a virtual environment with Python 3.8 (lower version of python may not work. The version we use is 3.8.5). 

**Step 1**: We need PyBind11 to link the C++ based sampler with the PyTorch based trainer. The `./shaDow/para_sampler/ParallelSampler.*` contains the C++ code for the PPR and k-hop samplers. The `./shaDow/para_sampler/pybind11/` directory contains a [copy of PyBind11](https://github.com/pybind/pybind11). 

Before training, we need to build the C++ sampler as a python package, so that it can be directly imported by the PyTorch trainer (just like we import any other python module). To do so, you need to install the following: 

* `cmake` (our version is 3.18.2. May be installed by `conda install -c anaconda cmake`)
* `ninja` (our version is 1.10.2. May be installed by `conda install -c conda-forge ninja`)
* `pybind11` (our version is 2.6.2. May be installed by `pip install pybind11`)
* `OpenMP`: normally openmp should already be included in the C++ compiler. If not, you may need to install it manually based on your C++ compiler version. 

Then build the sampler. Run the following in your terminal

```
bash install.sh
```

On Windows machine, you could instead execute `.\install.bat`.

NOTE: if the above does not work, we provide an alternative script to compile the C++ sampler. See the [Troubleshooting section](#Troubleshooting) for details. 


**Step 2**: Install all the other Python packages in your virtual environment. 

* pytorch==1.7.1 (CUDA 11)
* Pytorch Geometric and its dependency packages (torch-scatter, torch-sparse, etc.)
    * Follow the [official instructions](https://pytorch-geometric.readthedocs.io/en/latest/notes/installation.html) (see the "Installation via Binaries" section)
    * We also explicitly use the `torch_scatter` functions to perform some graph operations for shaDow. 
* ogb>=1.2.4
* dgl>=0.5.3 (only used by the postprocessing of C&S). Can be installed by `pip` or `conda`. See the [official instruction](https://docs.dgl.ai/install/index.html)
* numpy>=1.19.2
* scipy>=1.6.0
* scikit-learn>=0.24.0
* pyyaml>=5.4.1
* argparse
* tqdm

**Step 3**: Record your system information. We use the `CONFIG.yml` file to keep track of the meta information of your hardware / software system. Copy `CONFIG_TEMPLATE.yml` and name it `CONFIG.yml`. Edit the fields based on your machine specs. 

In most cases, the only thing you need to overwrite is the `max_threads` field. This is used to control the parallelism of the C++ sampler. You can also set it to `-1` so that OpenMP will automatically decide the number of threads for you. 

**Step 4**: You should be able to run the training now. In general, just type:

```
python -m shaDow.main --configs <your config *.yml file> --dataset <name of the graph> --gpu <index of the available GPU>
```

where the `*.yml` file specifies the GNN architecture, sampler parameters and other hyperparameters. The name of the graph should correspond to the sub-directory name under `./data/`. E.g., the graphs used in the papers correspond to `flickr`, `reddit`, `yelp`, `arxiv`, `products`, `papers100M` (we use all **lowercase** and omit the `ogbn-` prefix). 

**Step 5** Check the logs of the training. We use the following protocol for logging. Our rationale is that the logs enable you to completely reproduce your own previous runs. 

* Each run gets its own subdirectory in the format of `./<log dir>/<data>/<running|done|crashed|killed>/<timestamp>-<githash>/...`
    * When the training is still in progress, the logs are in the `running` directory. 
    * When the training finishes normally, the logs will be moved to the `finished` directory.
    * When the training is killed (e.g., by CTRL-C), the logs will be moved to the `killed` directory.
    * When the training crashes (e.g., bugs in the code), the logs will be moved to the `crashed` directory. 
* In the subdirectory we should find the following files:
    * `*.yml`: a copy of the `*.yml` file to launch the training
    * `epoch_<train|valid|test>.csv`: CSV file logging the accuracy of each epoch
    * `final.csv`: CSV file logging the final accuracy on the full train / valid / test sets. 
    * pytorch checkpoint: storing the model weights and optimizer states. 

## Reproducing the results

We first describe the command for a single run. At the end of this section, we introduce the wrapper script for repeating the same configuration for 10 runs. 

For the Table 2 results comparing with the leaderboard methods:

### `ogbn-arxiv`

**shaDow-SAGE**

Run the following:

```
python -m shaDow.main --configs config_train/arxiv/sage_5_ppr_ogb.yml --dataset arxiv --gpu 0
```

**shaDow-CS**

You need to first generate multiple (e.g., 10) checkpoints for the above shaDow-SAGE architecture. Then identify the subdirectories for those checkpoints, and update `config_postproc/arxiv/cs_ogb.yml` with those subdirectories. E.g., the updated `*.yml` may look something like this:

```yml
...
dir_pred_mat:
  - logs/arxiv/finished/2021-01-01 00-00-00-<githash>
  - logs/arxiv/finished/2021-01-01 00-00-01-<githash>
```

Then run the following:

```
python -m shaDow.main --postproc_configs config_postproc/arxiv/cs_ogb.yml --dataset arxiv --gpu 0 
```

### `ogbn-products`

**shaDow-GAT**

Run the following:

```
python -m shaDow.main --configs config_train/products/gat_5_ppr_ogb.yml --dataset products --log_test_convergence -1 --nocache test --gpu 0
```

(**Note**, we set a relatively large batch size in the provided `config_train/products/gat_5_ppr_ogb.yml` so that it better utilizes the hardware resources of more powerful GPUs. If your GPU memory is limited, please reduce the `batch_size` (and also learning rate correspondingly) in the yml file. )

**shaDow-CS**

You need to first generate multiple (e.g., 10) checkpoints for the above shaDow-GAT architecture. Then identify the subdirectories for those checkpoints, and update `config_postproc/products/cs_ogb.yml` with those subdirectories. E.g., the updated `*.yml` may look something like this:

```yml
...
dir_pred_mat:
  - logs/products/finished/2021-01-01 00-00-00-<githash>
  - logs/products/finished/2021-01-01 00-00-01-<githash>
```

Then run the following:

```
python -m shaDow.main --postproc_configs config_postproc/products/cs_ogb.yml --dataset products --gpu 0 
```

### `ogbn-papers100M`

**shaDow-SAGE**

Run the following:

```
python -m shaDow.main --configs config_train/papers100M/sage_5_ppr_ogb.yml --dataset papers100M --gpu 0
```

**shaDow-GAT**

Run the following:

```
python -m shaDow.main --configs config_train/papers100M/gat_5_ppr_ogb.yml --dataset papers100M --gpu 0
```

### Repeat the same configuration 10 times

According to the OGB leaderboard instruction, we need to repeat the same configuration for 10 times, and report the mean and std. We provide a wrapper script for this purpose: `./scripts/train_multiple_runs.py`. 

**NOTE**: the wrapper script uses python subprocess to launch multiple runs. There seems to be some issue on redirecting the print-out messages of the training subprocess. It may appear that the program stucks without any outputs. However, the training is actually running **in the background**. You can check the corresponding log files in the `running/` directory to see the accuracy per epoch being updated. 

For example, to repeat the `ogbn-arxiv` configuration for 10 times:

```
python scripts/train_multiple_runs.py --dataset arxiv --configs config_train/arxiv/sage_5_ppr_ogb.yml --gpu 0 --repetition 10
```

For `ogbn-products`:

```
python scripts/train_multiple_runs.py --dataset products --configs config_train/products/gat_5_ppr_ogb.yml --log_test_convergence -1 --nocache test --gpu 0 --repetition 10
```

where all the command line arguments are the same as the original training script (i.e., the `shaDow.main` module). The only additional flag is `--repetition`. 

## Troubleshooting

### What if the sampler does not build successfully? 

See this [installation instruction](https://github.com/pybind/pybind11/blob/stable/docs/basics.rst) to manually install PyBind11. i.e., do the following:
* Download pybind11 from the official GitHub repo. Enter the pybind11 root dir.
* Install `pytest` by, e.g., `pip install -U pytest`
* `mkdir build`
* `cd build`
* `cmake ..`
    * Note: if `cmake` is not installed, you can install it via Anaconda: `conda install -c anaconda cmake ` 
* `make check -j 4` (Windows machine does not seem to be well supported)
* Install `pybind11` package: `python -m pip install pybind11`

Then compile the C++ sampler by

```
./compile.sh
```