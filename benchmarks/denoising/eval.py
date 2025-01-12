from argparse import ArgumentParser
import copy
import json
import imageio
import numpy as np
from pathlib import Path
import torch
from tqdm import tqdm

from baselines import bilateral_depth_filter, laplace_depth_filter
from data import SLSDataset

from common import (
    hwc_to_chw, chw_to_hwc, Tiler, load_network, predict
)

if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--checkpoint', type=Path, required=True, help="Path to the model checkpoint")
    parser.add_argument('--dataset', type=Path, required=True, help="Directory of the evaluation dataset (must have same format as training dataset).")
    parser.add_argument('--output_dir', type=Path, default="./eval", help="Directory where evaluation results are written to.")
    parser.add_argument('--gpu_id', type=int, default=0, help="Id of the GPU to use.")
    args = parser.parse_args()

    # Select the device
    if args.gpu_id != -1 and torch.cuda.is_available():
        print(f"Using GPU {args.gpu_id}")
        device = torch.device(f'cuda:{args.gpu_id}')
    else:
        print(f"Using CPU")
        device = torch.device('cpu')
        args.gpu_id = -1

    # Load the network from a checkpoint
    if not args.checkpoint.exists():
        raise RuntimeError(f"Checkpoint {args.checkpoint} does not exist.")
    
    print(f"Loading checkpoint from '{args.checkpoint}'")
    network, checkpoint, config = load_network(args.checkpoint, device=device)
    network.eval()
    print(config)

    # Set up the evaluation dataset
    print(f"Loading dataset from '{args.checkpoint}'")
    data_config = config['input']['train']
    data_config['base_dir'] = str(args.dataset)
    data_config['repetitions'] = 1
    data_config['transform'] = None
    dataset = SLSDataset(**data_config, preload=False)
    # dataloader = torch.utils.data.DataLoader(dataset, pin_memory=args.gpu_id > 0, batch_size=1, shuffle=False)

    # TODO: This should be a parameter
    K = 0.5 * np.array([[ 14700.7978515625, 0.0, 3230.5765901904233],
                        [ 0.0, 14711.431640625, 2422.6457405752153],
                        [ 0.0, 0.0, 1.0]])           
    K[2, 2] = 1.0
    R = np.eye(3)
    t = np.zeros(3)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    denoisers = {
        'Scan': lambda x: x['depth'], 
        'CNN': lambda x: predict(network, x, device).cpu().numpy(),
        'Bilateral': lambda x: bilateral_depth_filter(x['depth'], 2.2583333333333333, 0.005),
        'Laplace': lambda x: laplace_depth_filter(x['depth'], K, R, t),
    }

    from metrics import AggregatedMeter, DepthMAE, DepthRMSE, NormalAngleDifference, NormalAnglePercentage
    metrics = {
        'MAE': DepthMAE(scale=1000),
        'RMSE': DepthRMSE(scale=1000),
        'NormalAngleDifference': NormalAngleDifference(K=K),
        'NormalAnglePercentage[3]': NormalAnglePercentage(3, K=K),
        'NormalAnglePercentage[5]': NormalAnglePercentage(5, K=K),
        'NormalAnglePercentage[15]': NormalAnglePercentage(10, K=K),
    }
    metric_meters = { k: AggregatedMeter(metrics) for k, v in denoisers.items() }

    progress_bar = tqdm(dataset)
    for sample_idx, sample in enumerate(progress_bar):
        progress_bar.set_description(desc=f'Sample {sample_idx}')

        for k, denoise in denoisers.items():
            prediction = denoise(sample)
            metric_meters[k].update(prediction, sample['target'], mask=(sample['depth'] > 0)[..., 0])
    
    for k, v in metric_meters.items():
        print(k)
        print(v.suffix)