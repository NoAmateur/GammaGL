import os
# os.environ['CUDA_VISIBLE_DEVICES'] = '0'
# os.environ['TL_BACKEND'] = 'torch'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
import sys
import argparse
sys.path.insert(0, os.path.abspath('./'))
import tensorlayerx as tlx
from gammagl.datasets import Planetoid
from gammagl.utils import mask_to_index
from gammagl.models import GNNLFHFModel
from tensorlayerx.model import TrainOneStep, WithLoss


class SemiSpvzLoss(WithLoss):
    def __init__(self, net, loss_fn):
        super(SemiSpvzLoss, self).__init__(backbone=net, loss_fn=loss_fn)

    def forward(self, data, y):
        logits = self.backbone_network(data['x'])
        train_logits = tlx.gather(logits, data['train_idx'])
        train_y = tlx.gather(data['y'], data['train_idx'])
        loss = self._loss_fn(train_logits, train_y)

        l2_reg = sum((tlx.reduce_sum(param ** 2) for param in self.backbone_network.reg_params))
        loss = loss + data["reg_lambda"] / 2 * l2_reg

        return loss


def calculate_acc(logits, y, metrics):
    """
    Args:
        logits: node logits
        y: node labels
        metrics: tensorlayerx.metrics
    Returns:
        rst
    """

    metrics.update(logits, y)
    rst = metrics.result()
    metrics.reset()
    return rst


def main(args):
    # load datasets
    if str.lower(args.dataset) not in ['cora','pubmed','citeseer']:
        raise ValueError('Unknown dataset: {}'.format(args.dataset))
    dataset = Planetoid(args.dataset_path, args.dataset)
    graph = dataset[0]

    # for mindspore, it should be passed into node indices
    train_idx = mask_to_index(graph.train_mask)
    test_idx = mask_to_index(graph.test_mask)
    val_idx = mask_to_index(graph.val_mask)

    net = GNNLFHFModel(in_channels = graph.num_features,
                       out_channels = dataset.num_classes,
                       hidden_dim = args.hidden_dim,
                       model_type = args.model_type,
                       model_form = args.model_form,
                       edge_index = graph.edge_index,
                       x = graph.x,
                       alpha = args.alpha,
                       mu = args.mu,
                       beta = args.beta,
                       niter = args.niter,
                       drop_rate = args.drop_rate,
                       num_layers = args.num_layers,
                       name = "GNNLFHF")

    optimizer = tlx.optimizers.Adam(lr=args.lr)
    metrics = tlx.metrics.Accuracy()
    train_weights = net.trainable_weights

    loss_func = SemiSpvzLoss(net, tlx.losses.softmax_cross_entropy_with_logits)
    train_one_step = TrainOneStep(loss_func, optimizer, train_weights)

    data = {
        "x": graph.x,
        "y": graph.y,
        "edge_index": graph.edge_index,
        "train_idx": train_idx,
        "test_idx": test_idx,
        "val_idx": val_idx,
        "num_nodes": graph.num_nodes,
        "reg_lambda": args.reg_lambda
    }

    best_val_acc = 0
    for epoch in range(args.n_epoch):
        net.set_train()
        train_loss = train_one_step(data, data['y'])
        net.set_eval()
        logits = net(data['x'])
        val_logits = tlx.gather(logits, data['val_idx'])
        val_y = tlx.gather(data['y'], data['val_idx'])
        val_acc = calculate_acc(val_logits, val_y, metrics)

        print("Epoch [{:0>3d}] ".format(epoch+1)\
              + "  train loss: {:.4f}".format(train_loss.item())\
              + "  val acc: {:.4f}".format(val_acc))

        # save best model on evaluation set
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            net.save_weights(args.best_model_path+net.name+".npz", format='npz_dict')

    net.load_weights(args.best_model_path+net.name+".npz", format='npz_dict')
    net.set_eval()
    logits = net(data['x'])
    test_logits = tlx.gather(logits, data['test_idx'])
    test_y = tlx.gather(data['y'], data['test_idx'])
    test_acc = calculate_acc(test_logits, test_y, metrics)
    print("Test acc:  {:.4f}".format(test_acc))


if __name__ == '__main__':
    # parameters setting
    parser = argparse.ArgumentParser()
    parser.add_argument("--lr", type=float, default=0.01, help="learnin rate")
    parser.add_argument("--n_epoch", type=int, default=200, help="number of epoch")
    parser.add_argument("--hidden_dim", type=int, default=64, help="dimention of hidden layers")
    parser.add_argument("--drop_rate", type=float, default=0.8, help="drop_rate")
    parser.add_argument("--num_layers", type=int, default=2, help="number of layers")
    parser.add_argument("--reg_lambda", type=float, default=5e-3, help="reg_lambda")
    parser.add_argument('--dataset', type=str, default='cora', help='dataset')
    parser.add_argument("--model_type", type=str, default=r'GNN-LF', help="GNN-LF or GNN-HF")
    parser.add_argument("--model_form", type=str, default=r'closed', help="closed or iterative")
    parser.add_argument("--dataset_path", type=str, default=r'./', help="path to save dataset")
    parser.add_argument("--best_model_path", type=str, default=r'./', help="path to save best model")
    parser.add_argument("--alpha", type=float, default=0.3, help="the value of alpha")
    parser.add_argument("--mu", type=float, default=0.1, help="the value of mu")
    parser.add_argument("--beta", type=float, default=0.1, help="the value of beta")
    parser.add_argument("--niter", type=int, default=20, help="the value of niter")
    parser.add_argument("--gpu", type=int, default=0)
    
    args = parser.parse_args()
    if args.gpu >= 0:
        tlx.set_device("GPU", args.gpu)
    else:
        tlx.set_device("CPU")

    main(args)
