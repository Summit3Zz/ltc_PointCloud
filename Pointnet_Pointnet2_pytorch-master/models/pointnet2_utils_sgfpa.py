import torch
import torch.nn as nn
import torch.nn.functional as F
from time import time
import numpy as np

def timeit(tag, t):
    print("{}: {}s".format(tag, time() - t))
    return time()

def pc_normalize(pc):
    l = pc.shape[0]
    centroid = np.mean(pc, axis=0)
    pc = pc - centroid
    m = np.max(np.sqrt(np.sum(pc**2, axis=1)))
    pc = pc / m
    return pc

def square_distance(src, dst):
    """
    Calculate Euclid distance between each two points.

    src^T * dst = xn * xm + yn * ym + zn * zm；
    sum(src^2, dim=-1) = xn*xn + yn*yn + zn*zn;
    sum(dst^2, dim=-1) = xm*xm + ym*ym + zm*zm;
    dist = (xn - xm)^2 + (yn - ym)^2 + (zn - zm)^2
         = sum(src**2,dim=-1)+sum(dst**2,dim=-1)-2*src^T*dst

    Input:
        src: source points, [B, N, C]
        dst: target points, [B, M, C]
    Output:
        dist: per-point square distance, [B, N, M]
    """
    B, N, _ = src.shape
    _, M, _ = dst.shape
    dist = -2 * torch.matmul(src, dst.permute(0, 2, 1))
    dist += torch.sum(src ** 2, -1).view(B, N, 1)
    dist += torch.sum(dst ** 2, -1).view(B, 1, M)
    return dist


def index_points(points, idx):
    """

    Input:
        points: input points data, [B, N, C]
        idx: sample index data, [B, S]
    Return:
        new_points:, indexed points data, [B, S, C]
    """
    device = points.device
    B = points.shape[0]
    view_shape = list(idx.shape)
    view_shape[1:] = [1] * (len(view_shape) - 1)
    repeat_shape = list(idx.shape)
    repeat_shape[0] = 1
    batch_indices = torch.arange(B, dtype=torch.long).to(device).view(view_shape).repeat(repeat_shape)
    new_points = points[batch_indices, idx, :]
    return new_points


def farthest_point_sample(xyz, npoint):
    """
    Input:
        xyz: pointcloud data, [B, N, 3]
        npoint: number of samples
    Return:
        centroids: sampled pointcloud index, [B, npoint]
    """
    device = xyz.device
    B, N, C = xyz.shape
    centroids = torch.zeros(B, npoint, dtype=torch.long).to(device)
    distance = torch.ones(B, N).to(device) * 1e10
    farthest = torch.randint(0, N, (B,), dtype=torch.long).to(device)
    batch_indices = torch.arange(B, dtype=torch.long).to(device)
    for i in range(npoint):
        centroids[:, i] = farthest
        centroid = xyz[batch_indices, farthest, :].view(B, 1, 3)
        dist = torch.sum((xyz - centroid) ** 2, -1)
        mask = dist < distance
        distance[mask] = dist[mask]
        farthest = torch.max(distance, -1)[1]
    return centroids


def query_ball_point(radius, nsample, xyz, new_xyz):
    """
    Input:
        radius: local region radius
        nsample: max sample number in local region
        xyz: all points, [B, N, 3]
        new_xyz: query points, [B, S, 3]
    Return:
        group_idx: grouped points index, [B, S, nsample]
    """
    device = xyz.device
    B, N, C = xyz.shape
    _, S, _ = new_xyz.shape
    group_idx = torch.arange(N, dtype=torch.long).to(device).view(1, 1, N).repeat([B, S, 1])
    sqrdists = square_distance(new_xyz, xyz)
    group_idx[sqrdists > radius ** 2] = N
    group_idx = group_idx.sort(dim=-1)[0][:, :, :nsample]
    group_first = group_idx[:, :, 0].view(B, S, 1).repeat([1, 1, nsample])
    mask = group_idx == N
    group_idx[mask] = group_first[mask]
    view_shape = list(group_idx.shape)
    view_shape[1:] = [1] * (len(view_shape) - 1)
    repeat_shape = list(group_idx.shape)
    repeat_shape[0] = 1
    batch_indices = torch.arange(B, dtype=torch.long).to(device).view(view_shape).repeat(repeat_shape)
    ashape=list(group_idx.shape)
    ashape[0]=1
    ashape[2]=1
    reshape=list(group_idx.shape)
    reshape[1]=1
    s_indices=torch.arange(S, dtype=torch.long).to(device).view(ashape).repeat(reshape)
    sq = sqrdists[batch_indices, s_indices,group_idx].view(B,S,nsample,1)
    return group_idx,sq


def sample_and_group(npoint, radius, nsample, xyz, points, returnfps=False):
    """
    Input:
        npoint:
        radius:
        nsample:
        xyz: input points position data, [B, N, 3]
        points: input points data, [B, N, D]
    Return:
        new_xyz: sampled points position data, [B, npoint, nsample, 3]
        new_points: sampled points data, [B, npoint, nsample, 3+D]
    """
    B, N, C = xyz.shape
    S = npoint
    fps_idx = farthest_point_sample(xyz, npoint) # [B, npoint, C]
    new_xyz = index_points(xyz, fps_idx)
    idx,sq = query_ball_point(radius, nsample, xyz, new_xyz)
    grouped_xyz = index_points(xyz, idx) # [B, npoint, nsample, C]
    grouped_xyz_norm = grouped_xyz - new_xyz.view(B, S, 1, C)
    yuan = new_xyz.view(B, S, 1, C).repeat(1, 1, nsample, 1)
    if points is not None:
        grouped_points = index_points(points, idx)
        new_points = torch.cat([grouped_xyz_norm, grouped_points,yuan,sq], dim=-1) # [B, npoint, nsample, C+D]
    else:
        new_points = grouped_xyz_norm
    if returnfps:
        return new_xyz, new_points, grouped_xyz, fps_idx
    else:
        return new_xyz, new_points


def sample_and_group_all(xyz, points,color,colors):
    """
    Input:
        xyz: input points position data, [B, N, 3]
        points: input points data, [B, N, D]
    Return:
        new_xyz: sampled points position data, [B, 1, 3]
        new_points: sampled points data, [B, 1, N, 3+D]
    """
    device = xyz.device
    B, N, C = xyz.shape
    new_xyz = torch.zeros(B, 1, C).to(device)
    grouped_xyz = xyz.view(B, 1, N, C)
    grouped_color=color.view(B, 1, N, C)
    if points is not None:
        new_points = torch.cat([grouped_xyz, points.view(B, 1, N, -1)], dim=-1)
        new_colors = torch.cat([grouped_color, colors.view(B, 1, N, -1)], dim=-1)
    else:
        new_points = grouped_xyz
    return new_xyz, new_points,new_colors


class PointNetSetAbstraction(nn.Module):
    def __init__(self, npoint, radius, nsample, in_channel, mlp, group_all):
        super(PointNetSetAbstraction, self).__init__()
        self.npoint = npoint
        self.radius = radius
        self.nsample = nsample
        self.mlp_convs = nn.ModuleList()
        self.mlp_bns = nn.ModuleList()
        self.mlp_convs2 = nn.ModuleList()
        self.mlp_bns2 = nn.ModuleList()
        last_channel = in_channel
        for out_channel in mlp:
            self.mlp_convs.append(nn.Conv2d(last_channel, out_channel, 1))
            self.mlp_bns.append(nn.BatchNorm2d(out_channel))
            self.mlp_convs2.append(nn.Conv2d(last_channel, out_channel, 1))
            self.mlp_bns2.append(nn.BatchNorm2d(out_channel))
            last_channel = out_channel
        self.group_all = group_all

    def forward(self, xyz, points,color,colors):
        """
        Input:
            xyz: input points position data, [B, C, N]
            points: input points data, [B, D, N]
        Return:
            new_xyz: sampled points position data, [B, C, S]
            new_points_concat: sample points feature data, [B, D', S]
        """
        xyz = xyz.permute(0, 2, 1)
        color=color.permute(0, 2, 1)
        if points is not None:
            points = points.permute(0, 2, 1)
            colors=colors.permute(0, 2, 1)
        if self.group_all:
            new_xyz, new_points,new_colors = sample_and_group_all(xyz, points,color,colors)
        else:
            new_xyz, new_points = sample_and_group(self.npoint, self.radius, self.nsample, xyz, points)
        # new_xyz: sampled points position data, [B, npoint, C]
        # new_points: sampled points data, [B, npoint, nsample, C+D]
        new_points = new_points.permute(0, 3, 2, 1) # [B, C+D, nsample,npoint]
        new_colors = new_colors.permute(0, 3, 2, 1)
        for i, conv in enumerate(self.mlp_convs):
            bn = self.mlp_bns[i]
            new_points =  F.relu(bn(conv(new_points)))
        for i, conv2 in enumerate(self.mlp_convs2):
            bn2 = self.mlp_bns2[i]
            new_colors =  F.relu(bn2(conv2(new_colors)))
        new_points = torch.max(new_points, 2)[0]
        new_colors = torch.max(new_colors, 2)[0]
        new_xyz = new_xyz.permute(0, 2, 1)
        new_color=new_xyz
        return new_xyz, new_points,new_color,new_colors


class PointNetSetAbstractionMsg(nn.Module):
    def __init__(self, npoint, radius_list, nsample_list, in_channel, mlp_list):
        super(PointNetSetAbstractionMsg, self).__init__()
        self.npoint = npoint
        self.radius_list = radius_list
        self.nsample_list = nsample_list
        self.conv_blocks = nn.ModuleList()
        self.bn_blocks = nn.ModuleList()
        self.conv_blocks2 = nn.ModuleList()
        self.bn_blocks2 = nn.ModuleList()
        for i in range(len(mlp_list)):
            convs = nn.ModuleList()
            bns = nn.ModuleList()
            convs2 = nn.ModuleList()
            bns2 = nn.ModuleList()
            last_channel = in_channel + 7
            last_channel2=in_channel+6
            for out_channel in mlp_list[i]:
                convs.append(nn.Conv2d(last_channel, out_channel, 1))
                bns.append(nn.BatchNorm2d(out_channel))
                convs2.append(nn.Conv2d(last_channel2, out_channel, 1))
                bns2.append(nn.BatchNorm2d(out_channel))
                last_channel = out_channel
                last_channel2 = out_channel
            self.conv_blocks.append(convs)
            self.bn_blocks.append(bns)
            self.conv_blocks2.append(convs2)
            self.bn_blocks2.append(bns2)

    def forward(self, xyz, points,color,colors):
        """
        Input:
            xyz: input points position data, [B, C, N]
            points: input points data, [B, D, N]
        Return:
            new_xyz: sampled points position data, [B, C, S]
            new_points_concat: sample points feature data, [B, D', S]
        """
        xyz = xyz.permute(0, 2, 1)
        color=color.permute(0, 2, 1)
        points = points.permute(0, 2, 1)
        colors=colors.permute(0, 2, 1)
        B, N, C = xyz.shape
        S = self.npoint
        new_xyz = index_points(xyz, farthest_point_sample(xyz, S))
        new_color = index_points(color, farthest_point_sample(xyz, S))
        new_points_list = []
        new_colors_list = []
        for i, radius in enumerate(self.radius_list):
            K = self.nsample_list[i]
            group_idx,sq = query_ball_point(radius, K, xyz, new_xyz)
            grouped_xyz = index_points(xyz, group_idx)
            grouped_color=index_points(color, group_idx)
            grouped_xyz -= new_xyz.view(B, S, 1, C)
            grouped_color-=new_color.view(B, S, 1, C)
            yuan = new_xyz.view(B, S, 1, C).repeat(1, 1, K, 1)
            yuancolor=new_color.view(B, S, 1, C).repeat(1, 1, K, 1)
            if points is not None:
                grouped_points = index_points(points, group_idx)
                grouped_points = torch.cat([grouped_points, grouped_xyz,yuan,sq], dim=-1)
                grouped_color1 = index_points(colors, group_idx)
                grouped_color1 = torch.cat([grouped_color1, grouped_color, yuancolor], dim=-1)
            else:
                grouped_points = grouped_xyz
                grouped_color1=grouped_color
            grouped_points = grouped_points.permute(0, 3, 2, 1)  # [B, D, K, S]
            grouped_color1 = grouped_color1.permute(0, 3, 2, 1)  # [B, D, K, S]
            for j in range(len(self.conv_blocks[i])):
                conv = self.conv_blocks[i][j]
                bn = self.bn_blocks[i][j]
                grouped_points =  F.relu(bn(conv(grouped_points)))
                conv2 = self.conv_blocks2[i][j]
                bn2 = self.bn_blocks2[i][j]
                grouped_color1 = F.relu(bn2(conv2(grouped_color1)))
            new_points = torch.max(grouped_points, 2)[0]  # [B, D', S]
            new_colors = torch.max(grouped_points, 2)[0]  # [B, D', S]
            new_points_list.append(new_points)
            new_colors_list.append(new_colors)
        new_xyz = new_xyz.permute(0, 2, 1)
        new_color=new_color.permute(0,2,1)
        new_points_concat = torch.cat(new_points_list, dim=1)
        new_colors_concat = torch.cat(new_points_list, dim=1)
        return new_xyz, new_points_concat,new_color,new_colors_concat


class PointNetFeaturePropagation(nn.Module):
    def __init__(self, in_channel, mlp):
        super(PointNetFeaturePropagation, self).__init__()
        self.mlp_convs = nn.ModuleList()
        self.mlp_bns = nn.ModuleList()
        self.mlp_convs2 = nn.ModuleList()
        self.mlp_bns2 = nn.ModuleList()
        last_channel = in_channel
        for out_channel in mlp:
            self.mlp_convs.append(nn.Conv1d(last_channel, out_channel, 1))
            self.mlp_bns.append(nn.BatchNorm1d(out_channel))
            self.mlp_convs2.append(nn.Conv1d(last_channel, out_channel, 1))
            self.mlp_bns2.append(nn.BatchNorm1d(out_channel))
            last_channel = out_channel

    def forward(self, xyz1, xyz2, points1, points2,colors1,colors2):
        """
        Input:
            xyz1: input points position data, [B, C, N]
            xyz2: sampled input points position data, [B, C, S]
            points1: input points data, [B, D, N]
            points2: input points data, [B, D, S]
        Return:
            new_points: upsampled points data, [B, D', N]
        """
        xyz1 = xyz1.permute(0, 2, 1)
        xyz2 = xyz2.permute(0, 2, 1)
        points2 = points2.permute(0, 2, 1)
        colors2=colors2.permute(0, 2, 1)
        B, N, C = xyz1.shape
        _, S, _ = xyz2.shape

        if S == 1:
            interpolated_points = points2.repeat(1, N, 1)
            interpolated_colors = colors2.repeat(1, N, 1)
        else:
            dists = square_distance(xyz1, xyz2)
            dists, idx = dists.sort(dim=-1)
            dists, idx = dists[:, :, :3], idx[:, :, :3]  # [B, N, 3]

            dist_recip = 1.0 / (dists + 1e-8)
            norm = torch.sum(dist_recip, dim=2, keepdim=True)
            weight = dist_recip / norm
            interpolated_points = torch.sum(index_points(points2, idx) * weight.view(B, N, 3, 1), dim=2)
            interpolated_colors = torch.sum(index_points(colors2, idx) * weight.view(B, N, 3, 1), dim=2)
        if points1 is not None:
            points1 = points1.permute(0, 2, 1)
            colors1=colors1.permute(0, 2, 1)
            new_points = torch.cat([points1, interpolated_points], dim=-1)
            new_colors=torch.cat([colors1, interpolated_colors], dim=-1)
        else:
            new_points = interpolated_points
            new_colors=interpolated_colors

        new_points = new_points.permute(0, 2, 1)
        new_colors=new_colors.permute(0, 2, 1)
        for i, conv in enumerate(self.mlp_convs):
            bn = self.mlp_bns[i]
            new_points = F.relu(bn(conv(new_points)))
        for i, conv2 in enumerate(self.mlp_convs2):
            bn2 = self.mlp_bns2[i]
            new_colors= F.relu(bn2(conv2(new_colors)))
        return new_points,new_colors

