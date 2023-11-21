import argparse
import torch
import numpy as np
from numpy import *

ITERATION_STEPS = 5


# compute rollout between attention layers
def compute_rollout_attention(all_layer_matrices, start_layer=0):
    # adding residual consideration- code adapted from https://github.com/samiraabnar/attention_flow
    num_tokens = all_layer_matrices[0].shape[1]
    batch_size = all_layer_matrices[0].shape[0]
    eye = torch.eye(num_tokens).expand(batch_size, num_tokens, num_tokens).to(all_layer_matrices[0].device)
    all_layer_matrices = [all_layer_matrices[i] + eye for i in range(len(all_layer_matrices))]
    matrices_aug = [all_layer_matrices[i] / all_layer_matrices[i].sum(dim=-1, keepdim=True)
                    for i in range(len(all_layer_matrices))]
    joint_attention = matrices_aug[start_layer]
    for i in range(start_layer + 1, len(matrices_aug)):
        joint_attention = matrices_aug[i].bmm(joint_attention)
    return joint_attention


class LRP:
    def __init__(self, model):
        self.model = model
        self.model.eval()

    def generate_LRP(self, input, index=None, method="transformer_attribution", is_ablation=False, start_layer=0):
        output = self.model(input)
        kwargs = {"alpha": 1}
        if index == None:
            index = np.argmax(output.cpu().data.numpy(), axis=-1)

        one_hot = torch.zeros(1, output.size()[-1]).to('cuda')
        one_hot[0, index] = 1
        one_hot_vector = one_hot
        one_hot = torch.sum(one_hot * output)

        self.model.zero_grad()
        one_hot.backward(retain_graph=True)

        return self.model.relprop(one_hot_vector.to(input.device), method=method, is_ablation=is_ablation,
                                  start_layer=start_layer, **kwargs)


def get_interpolated_values(baseline, target, num_steps):
    """this function returns a list of all the images interpolation steps."""
    if num_steps <= 0: return np.array([])
    if num_steps == 1: return np.array([baseline, target])

    delta = target - baseline

    if baseline.ndim == 3:
        scales = np.linspace(0, 1, num_steps + 1, dtype=np.float32)[:, np.newaxis, np.newaxis,
                 np.newaxis]
    elif baseline.ndim == 4:
        scales = np.linspace(0, 1, num_steps + 1, dtype=np.float32)[:, np.newaxis, np.newaxis, np.newaxis, np.newaxis]
    elif baseline.ndim == 5:
        scales = np.linspace(0, 1, num_steps + 1, dtype=np.float32)[:, np.newaxis, np.newaxis, np.newaxis, np.newaxis,
                 np.newaxis]
    elif baseline.ndim == 6:
        scales = np.linspace(0, 1, num_steps + 1, dtype=np.float32)[:, np.newaxis, np.newaxis, np.newaxis, np.newaxis,
                 np.newaxis, np.newaxis]

    shape = (num_steps + 1,) + delta.shape
    deltas = scales * np.broadcast_to(delta.detach().numpy(), shape)
    interpolated_activations = baseline + deltas

    return interpolated_activations


class Baselines:
    def __init__(self, model):
        self.model = model
        self.model.eval()

    def generate_cam_attn(self, input, index=None):
        output = self.model(input.cuda(), register_hook=True)
        if index == None:
            index = np.argmax(output.cpu().data.numpy())

        one_hot = np.zeros((1, output.size()[-1]), dtype=np.float32)
        one_hot[0][index] = 1
        one_hot = torch.from_numpy(one_hot).requires_grad_(True)
        one_hot = torch.sum(one_hot.cuda() * output)

        self.model.zero_grad()
        one_hot.backward(retain_graph=True)

        grad = self.model.blocks[-1].attn.get_attn_gradients()
        cam = self.model.blocks[-1].attn.get_attention_map()
        cam = cam[0, :, 0, 1:].reshape(-1, 14, 14)
        grad = grad[0, :, 0, 1:].reshape(-1, 14, 14)
        grad = grad.means(dim=[1, 2], keepdim=True)
        cam = (cam * grad).means(0).clamp(min=0)
        cam = (cam - cam.min()) / (cam.max() - cam.min())

        return cam

    def generate_rollout(self, input, start_layer=0):
        self.model(input)
        blocks = self.model.blocks
        all_layer_attentions = []
        for blk in blocks:
            attn_heads = blk.attn.get_attention_map()
            avg_heads = (attn_heads.sum(dim=1) / attn_heads.shape[1]).detach()
            all_layer_attentions.append(avg_heads)
        rollout = compute_rollout_attention(all_layer_attentions, start_layer=start_layer)
        return rollout[:, 0, 1:]

    def generate_rollout_grads(self, input, start_layer=0):
        self.model(input)
        blocks = self.model.blocks
        all_layer_attentions = []
        for blk in blocks:
            attn_heads = blk.attn.get_attention_map()
            attn_grads = blk.attn.get_attn_gradients()
            print(attn_heads.shape)
            attn_heads = attn_heads * attn_grads
            avg_heads = (attn_heads.sum(dim=1) / attn_heads.shape[1]).detach()
            all_layer_attentions.append(avg_heads)
        rollout = compute_rollout_attention(all_layer_attentions, start_layer=start_layer)
        return rollout[:, 0, 1:]


    def do_backward(self, index, input):
        output = self.model(input.cuda(), register_hook=True)
        if index == None:
            index = np.argmax(output.cpu().data.numpy())
        one_hot = np.zeros((1, output.size()[-1]), dtype=np.float32)
        one_hot[0][index] = 1
        one_hot = torch.from_numpy(one_hot).requires_grad_(True)
        one_hot = torch.sum(one_hot.cuda() * output)
        self.model.zero_grad()
        one_hot.backward(retain_graph=True)
