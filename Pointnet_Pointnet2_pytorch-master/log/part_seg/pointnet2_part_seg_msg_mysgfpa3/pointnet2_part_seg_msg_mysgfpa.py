import torch.nn as nn
import torch
import torch.nn.functional as F
from models.pointnet2_utils_sg3 import PointNetSetAbstractionMsg,PointNetSetAbstraction,PointNetFeaturePropagation,PointNetSetAbstractionMsgfpa



class get_model(nn.Module):
    def __init__(self, num_classes, normal_channel=False):
        super(get_model, self).__init__()
        if normal_channel:
            additional_channel = 3
        else:
            additional_channel = 0
        self.normal_channel = normal_channel
        self.sa1 = PointNetSetAbstractionMsgfpa(512, [0.1, 0.2, 0.4], [32, 64, 128], 3, [[32, 32, 64], [64, 64, 128], [64, 96, 128]])
        self.sa2 = PointNetSetAbstractionMsgfpa(128, [0.4,0.8], [64, 128], 128+128+64, [[128, 128, 256], [128, 196, 256]])
        self.sa3 = PointNetSetAbstraction(npoint=None, radius=None, nsample=None, in_channel=512 + 3, mlp=[256, 512, 1024], group_all=True)
        self.fp3 = PointNetFeaturePropagation(in_channel=1536, mlp=[256, 256])
        self.fp2 = PointNetFeaturePropagation(in_channel=576, mlp=[256, 128])
        self.fp1 = PointNetFeaturePropagation(in_channel=131, mlp=[128, 128])
        self.conv1 = nn.Conv1d(256, 128, 1)
        self.bn1 = nn.BatchNorm1d(128)
        self.drop1 = nn.Dropout(0.5)
        self.conv2 = nn.Conv1d(128, num_classes, 1)
        self.num_classes=num_classes
    def forward(self, xyz, cls_label):
        # Set Abstraction layers
        B,C,N = xyz.shape

        if self.normal_channel:
            l0_points = xyz[:,:3,:]
            l0_xyz = xyz[:,:3,:]
            l0_color = xyz[:, 3:, :]
            l0_colors = xyz[:,3:,:]
        else:
            l0_points = xyz
            l0_xyz = xyz
        l1_xyz, l1_points,l1_color,l1_colors = self.sa1(l0_xyz, l0_points,l0_color,l0_colors)
        l2_xyz, l2_points,l2_color,l2_colors = self.sa2(l1_xyz, l1_points,l1_color,l1_colors)
        l3_xyz, l3_points,l3_color,l3_colors = self.sa3(l2_xyz, l2_points,l2_color,l2_colors)
        # Feature Propagation layers
        l2_points,l2_colors = self.fp3(l2_xyz, l3_xyz, l2_points, l3_points,l2_colors,l3_colors)
        l1_points,l1_colors = self.fp2(l1_xyz, l2_xyz, l1_points, l2_points,l1_colors,l2_colors)
        cls_label_one_hot = cls_label.view(B,self.num_classes,-1)
        l0_points,l0_colors = self.fp1(l0_xyz, l1_xyz, l0_points, l1_points,l0_colors,l1_colors)
        # FC layers
        l0_points=torch.cat([l0_points,l0_colors],1)
        feat = F.relu(self.bn1(self.conv1(l0_points)))
        x = self.drop1(feat)
        x = self.conv2(x)
        x = F.log_softmax(x, dim=1)
        x = x.permute(0, 2, 1)
        l3_points=torch.cat([l3_points,l3_colors],1)
        return x, l3_points


class get_loss(nn.Module):
    def __init__(self):
        super(get_loss, self).__init__()

    def forward(self, pred, target, trans_feat):
        total_loss = F.nll_loss(pred, target)

        return total_loss