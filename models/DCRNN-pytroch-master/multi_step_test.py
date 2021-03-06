# -*- coding: utf-8 -*-
"""
Created on Sun Aug  9 23:51:48 2020

@author: Ming Jin
"""

import math
import tqdm
import torch
import torch.nn as nn
import pandas as pd
import numpy as np
import argparse
import sys
import time

import utils
from net import DCRNNModel


# saving print statements in a log file
def log_string(log, string):
    log.write(string + '\n')
    log.flush()
    print(string)

"""
Hyperparameters same as training
"""
batch_size = 64
enc_input_dim = 2
dec_input_dim = 1
hidden_dim = 64
output_dim = 1
diffusion_steps = 2
rnn_layers = 2
seq_length = 12
horizon = 12
cl_decay_steps = 2000  # decrease teaching force ratio in global steps
filter_type = "dual_random_walk"

# parse arguments, default set to METR-LA dataset
parser = argparse.ArgumentParser()
parser.add_argument('--num_nodes',type=int,default=207,help='number of nodes')
parser.add_argument('--data',type=str,default='data/METR-LA',help='data path')
parser.add_argument('--checkpoints',type=str,default='./checkpoints/METR-LA/dcrnn.pt',help='checkpoints path')
parser.add_argument('--sensor_ids',type=str,default='./data/sensor_graph/graph_sensor_ids.txt',help='sensor ids path')
parser.add_argument('--sensor_distance',type=str,default='./data/sensor_graph/distances_la_2012.csv',help='sensor graph path')
parser.add_argument('--recording',type=str,default='./data/processed/METR-LA',help='sensor graph path')
parser.add_argument('--log_test', default='log_DCRNN_test_la.txt',help='log file')
args = parser.parse_args()

num_nodes = args.num_nodes
checkpoints = args.checkpoints
sensor_ids = args.sensor_ids
sensor_distance = args.sensor_distance
recording = args.recording
log = open(args.log_file, 'w')



"""
Dataset

"""
t1 = time.time()
# read sensor IDs
with open(sensor_ids) as f:
    sensor_ids = f.read().strip().split(',')

# read sensor distance
distance_df = pd.read_csv(sensor_distance, dtype={'from': 'str', 'to': 'str'})

# build adj matrix based on equation (10)
adj_mx = utils.get_adjacency_matrix(distance_df, sensor_ids)

data = utils.load_dataset(dataset_dir=recording, batch_size=batch_size, test_batch_size=batch_size)
test_data_loader = data['test_loader']
standard_scaler = data['scaler']

num_test_iteration_per_epoch = math.ceil(data['x_test'].shape[0] / batch_size)


"""
Restore model from the checkpoint

"""
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

model = DCRNNModel(adj_mx, 
                    diffusion_steps, 
                    num_nodes, 
                    batch_size, 
                    enc_input_dim, 
                    dec_input_dim, 
                    hidden_dim, 
                    output_dim,
                    rnn_layers,
                    filter_type).to(device)

cp = torch.load(checkpoints, map_location ='cpu')
model.load_state_dict(cp)

model = model.to(device)
model.eval()


"""
Testing and logging
"""

y_preds = torch.FloatTensor([]).to(device)
# [total_instances, horizon, nodes, dec_input_dim]
y_truths = data['y_test']
y_truths = standard_scaler.inverse_transform(y_truths)
predictions = []
groundtruth = list()

with torch.no_grad():
    for i, (x, y) in tqdm.tqdm(enumerate(test_data_loader.get_iterator()), total=num_test_iteration_per_epoch):
        x = torch.FloatTensor(x).to(device)
        y = torch.FloatTensor(y).to(device)
        outputs = model(x, y, 0)  # (horizon, batch_size, num_nodes*output_dim)
        y_preds = torch.cat([y_preds, outputs], dim=1)

# [horizon, total_instances, num_nodes*output_dim] --> # [total_instances, horizon, nodes, output_dim]
y_preds = torch.transpose(torch.reshape(y_preds, (horizon, -1, num_nodes, output_dim)), 0, 1)
y_preds = y_preds.detach().cpu().numpy()  # cast to numpy array


log_string(log, "--------multi-step testing results--------")
for horizon_i in range(1, y_truths.shape[1] + 1):
    y_truth = y_truths[:, :horizon_i, :, :output_dim]
    y_pred = standard_scaler.inverse_transform(y_preds[:, :horizon_i, :, :])
    predictions.append(y_pred)
    groundtruth.append(y_truth)
    mae = utils.masked_mae_np(y_pred[:y_truth.shape[0]], y_truth, null_val=0.0)
    mape = utils.masked_mape_np(y_pred[:y_truth.shape[0]], y_truth, null_val=0.0)
    rmse = utils.masked_rmse_np(y_pred[:y_truth.shape[0]], y_truth, null_val=0.0)

    log_string(log, "Horizon {:02d}, MAE: {:.2f}, MAPE: {:.4f}, RMSE: {:.2f}".format(
            horizon_i, mae, mape, rmse))

t2 = time.time()
log_string("Total time spent: {:.2f} minutes".format((t2-t1)/60))
