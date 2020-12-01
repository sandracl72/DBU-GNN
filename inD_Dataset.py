import numpy as np
import dgl
import random
import pickle
from scipy import spatial
import torch
import torch.nn as nn
import torch.nn.functional as F
import os
os.environ['DGLBACKEND'] = 'pytorch'
from torchvision import datasets, transforms
import scipy.sparse as spp
from dgl.data import DGLDataset
from sklearn.preprocessing import StandardScaler

history_frames = 5 # 5 second * 1 frame/second
future_frames = 3 # 3 second * 1 frame/second
total_frames = history_frames + future_frames
neighbor_distance = 10
max_num_object = 120 #per frame
total_feature_dimension = 7 #pos,heading,vel,class, mask

class inD_DGLDataset(torch.utils.data.Dataset):

    def __init__(self, train_val, data_path=None):
        self.raw_dir='/home/sandra/PROGRAMAS/DBU_Graph/data/ind_data.pkl'
        self.train_val=train_val
        self.process()        

    def load_data(self):
        with open(self.raw_dir, 'rb') as reader:
            [all_feature, self.all_adjacency, self.all_mean_xy]= pickle.load(reader)
        all_feature=np.transpose(all_feature, (0,3,2,1)) #(N,V,T,C)
        #Choose frames in each sequence
        self.all_feature=torch.from_numpy(all_feature[:,:70,:total_frames,:]).type(torch.float32)


    def process(self):
        #process data to graph, labels, and splitting masks
        self.load_data()
        total_num = len(self.all_feature)
        
        self.last_vis_obj=[]   #contains number of visible objects in each sequence of the training, i.e. objects in frame 5
        #para hacer grafos de tamaño variable
        for idx in range(len(self.all_adjacency)): 
            for i in range(len(self.all_adjacency[idx])): 
                if self.all_adjacency[idx][i,i] == 0:
                    self.last_vis_obj.append(i)
                    break   
        
        now_history_frame=history_frames-1
        feature_id = [0,1,2,3,4] #pos heading vel
        object_type = self.all_feature[:,:,:,5].int()  # torch Tensor NxVxT
        mask_car=np.zeros((total_num,self.all_feature.shape[1],total_frames)) #NxVx12
        for i in range(total_num):
            mask_car_t=np.array([1 if (j==1) else 0 for j in object_type[i,:,now_history_frame]])
            mask_car[i,:]=np.array(mask_car_t).reshape(mask_car.shape[1],1)+np.zeros(total_frames) #120x12

        #rescale_xy=torch.ones((1,1,1,2))
        #rescale_xy[:,:,:,0] = torch.max(abs(self.all_feature[:,:,:,3]))
        #rescale_xy[:,:,:,1] = torch.max(abs(self.all_feature[:,:,:,4]))

        #self.all_feature[:,:,:now_history_frame,3:5] = self.all_feature[:,:,:now_history_frame,3:5]/rescale_xy
        self.node_features = self.all_feature[:,:,:history_frames,feature_id]  #obj type,x,y 6 primeros frames
        self.node_labels=self.all_feature[:,:,history_frames:,feature_id] #x,y 6 ultimos frames
        
        '''
        scaler=StandardScaler()
        
        scale_xy=self.node_features[:,:,:,:2].reshape(self.node_features[:,:,:,:2].shape[0]*self.node_features[:,:,:,:2].shape[1],-1)  #NxV,T*C(x,y)
        scaler.fit(scale_xy)
        scaler.transform(scale_xy)
        self.node_features[:,:,:,:2] = scale_xy.view(self.node_features.shape[0],self.node_features.shape[1],now_history_frame,2)
        '''
        self.output_mask= self.all_feature[:,:,:,-1]*mask_car #mascara obj (car) visibles en 6º frame (5010,120,T_hist)
        self.output_mask = np.array(self.output_mask.unsqueeze_(-1) )  #(5010,120,T_hist,1)

        #EDGES weights  #5010x120x120[]
        self.xy_dist=[spatial.distance.cdist(self.node_features[i][:,now_history_frame,:], self.node_features[i][:,now_history_frame,:]) for i in range(len(self.all_feature))]  #5010x70x70

        # TRAIN VAL SETS
        # Remove empty rows from output mask 
        zero_indeces_list = [i for i in range(len(self.output_mask[:,:,history_frames:])) if np.all(np.array(self.output_mask[:,:,history_frames:].squeeze(-1))==0, axis=(1,2))[i] == True ]

        id_list = list(set(list(range(total_num))) - set(zero_indeces_list))
        total_valid_num = len(id_list)
        #ind=np.random.permutation(id_list)
        ind = id_list
        self.train_id_list, self.val_id_list, self.test_id_list = ind[:round(total_valid_num*0.7)], ind[round(total_valid_num*0.7):round(total_valid_num*0.9)],ind[round(total_valid_num*0.9):]

        #train_id_list = list(np.linspace(0, total_num-1, int(total_num*0.8)).astype(int))
        #val_id_list = list(set(list(range(total_num))) - set(train_id_list))  


    def __len__(self):
        if self.train_val.lower() == 'train':
            return len(self.train_id_list)
        elif self.train_val.lower() == 'val':
            return len(self.val_id_list)
        else:
            return len(self.test_id_list)

    def __getitem__(self, idx):
        if self.train_val.lower() == 'train':
            idx = self.train_id_list[idx]
        elif self.train_val.lower() == 'val':
            idx = self.val_id_list[idx]
        else:
            idx = self.test_id_list[idx]
        graph = dgl.from_scipy(spp.coo_matrix(self.all_adjacency[idx][:self.last_vis_obj[idx],:self.last_vis_obj[idx]])).int()
        graph = dgl.remove_self_loop(graph)
        '''
        for n in graph.nodes():
            if graph.in_degrees(n) == 0:
                graph.add_edges(n,n)
        '''
        '''
        #Data Augmentation
        if self.train_val.lower() == 'train' and np.random.random()>0.5:
            angle = 2 * np.pi * np.random.random()
            sin_angle = np.sin(angle)
            cos_angle = np.cos(angle)

            angle_mat = np.array(
                [[cos_angle, -sin_angle],
                [sin_angle, cos_angle]])

            xy = self.node_features[idx,:self.last_vis_obj[idx],:,:2]   #(V,T,C)
            #num_xy = np.sum(xy.sum(axis=-1).sum(axis=-1) != 0) # get the number of valid data

            # angle_mat: (2, 2), xy: (2, 12, 120)
            out_xy = np.einsum('ab,vtb->vta', angle_mat, xy)
            #now_mean_xy = np.matmul(angle_mat, now_mean_xy)
            xy= out_xy

            self.node_features[idx,:self.last_vis_obj[idx],:,:2] = torch.from_numpy(xy).type(torch.float32)
        '''

        graph = dgl.add_self_loop(graph)
        distances = [self.xy_dist[idx][graph.edges()[0][i]][graph.edges()[1][i]] for i in range(graph.num_edges())]
        norm_distances = [(i-min(distances))/(max(distances)-min(distances)) if (max(distances)-min(distances))!=0 else (i-min(distances))/1.0 for i in distances]
        norm_distances = [1/(i) if i!=0 else 1 for i in distances]
        graph.edata['w']=torch.tensor(norm_distances, dtype=torch.float32)
        graph.ndata['x']=self.node_features[idx,:self.last_vis_obj[idx]] 
        graph.ndata['gt']=self.node_labels[idx,:self.last_vis_obj[idx]]
        output_mask = self.output_mask[idx,:self.last_vis_obj[idx]]
        
        return graph, output_mask