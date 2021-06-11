import os
import argparse
import random
from tqdm import tqdm
import csv
import torch
from torch.utils.data import DataLoader
import albumentations as A
from albumentations.pytorch import ToTensorV2

from metrics import word_error_rate, sentence_acc, final_metric
from checkpoint import load_checkpoint
from dataset import LoadEvalDataset, collate_eval_batch, START, PAD
from train import get_valid_transforms
from flags import Flags
from utils import id_to_string, get_network, get_optimizer, set_seed
from decoding import decode
from postprocessing import get_decoding_manager


def main(parser):

    is_cuda = torch.cuda.is_available()
    checkpoint = load_checkpoint(parser.checkpoint, cuda=is_cuda)
    options = Flags(checkpoint["configs"]).get()
    set_seed(options.seed)

    hardware = "cuda" if is_cuda else "cpu"
    device = torch.device(hardware)
    print("--------------------------------")
    print("Running {} on device {}\n".format(options.network, device))

    model_checkpoint = checkpoint["model"]
    if model_checkpoint:
        print(
            "[+] Checkpoint\n",
            "Resuming from epoch : {}\n".format(checkpoint["epoch"]),
        )

    transformed = get_valid_transforms(
        height=options.input_size.height, width=options.input_size.width
    )
    dummy_gt = "\sin " * parser.max_sequence  # set maximum inference sequence

    root = os.path.join(os.path.dirname(parser.file_path), "images")
    with open(parser.file_path, "r") as fd:
        reader = csv.reader(fd, delimiter="\t")
        data = list(reader)

    test_data = [[os.path.join(root, x[0]), x[0], dummy_gt] for x in data]
    test_dataset = LoadEvalDataset(
        test_data,
        checkpoint["token_to_id"],
        checkpoint["id_to_token"],
        crop=False,
        transform=transformed,
        rgb=options.data.rgb,
    )
    test_data_loader = DataLoader(
        test_dataset,
        batch_size=parser.batch_size,
        shuffle=False,
        num_workers=options.num_workers,
        collate_fn=collate_eval_batch,
    )

    print(
        "[+] Data\n",
        "The number of test samples : {}\n".format(len(test_dataset)),
    )
    manager = (
        get_decoding_manager(tokens_path="./configs/tokens.txt", batch_size=parser.batch_size)
        if parser.decoding_manager
        else None
    )
    
    model = get_network(
        model_type=options.network,
        FLAGS=options,
        model_checkpoint=model_checkpoint,
        device=device,
        dataset=test_dataset,
        decoding_manager=manager
    )
    model.eval()
    results = []
    print("[+] Decoding Type:", parser.decode_type)

    with torch.no_grad():
        for d in tqdm(test_data_loader):
            input = d["image"].float().to(device)
            expected = d["truth"]["encoded"].to(device)
            sequence = decode(
                model=model,
                input=input,
                data_loader=test_data_loader,
                expected=expected,
                method=parser.decode_type,
                beam_width=parser.beam_width,
            )
            sequence_str = id_to_string(sequence, test_data_loader, do_eval=1)

            for path, predicted in zip(d["file_path"], sequence_str):
                results.append((path, predicted))

    os.makedirs(parser.output_dir, exist_ok=True)
    with open(os.path.join(parser.output_dir, "output.csv"), "w") as w:
        for path, predicted in results:
            w.write(path + "\t" + predicted + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inference_type",
        default="single",
        type=str,
        help="추론 방법 설정 'single(단일모델추론)', 'ensemble앙상블()'",
    )
    parser.add_argument(
        "--checkpoint",
        dest="checkpoint",
        default="./log/my_satrn/checkpoints/0.7907 F0 dual opt MySATRN_best_model.pth",
        type=str,
        help="Path of checkpoint file",
    )
    parser.add_argument(
        "--max_sequence",
        dest="max_sequence",
        default=230,
        type=int,
        help="maximun sequence when doing inference",
    )
    parser.add_argument(
        "--batch_size",
        dest="batch_size",
        default=128,
        type=int,
        help="batch size when doing inference",
    )
    parser.add_argument(
        "--decode_type",
        dest="decode_type",
        default="greedy",  # 'greedy'로 설정하면 기존과 동일하게 inference
        type=str,
        help="디코딩 방식 설정. 'greedy', 'beam'",
    )
    parser.add_argument(
        "--beam_width",
        dest="beam_width",
        default=3,
        type=int,
        help="빔서치 사용 시 스텝별 후보 수 설정",
    )
    parser.add_argument(
        "--decoding_manager", default=True, type=bool, help="DecodingManager 활용 여부 설정"
    )
    eval_dir = os.environ.get("SM_CHANNEL_EVAL", "/opt/ml/input/data/")
    file_path = os.path.join(eval_dir, "eval_dataset/input.txt")
    parser.add_argument(
        "--file_path",
        dest="file_path",
        default=file_path,
        type=str,
        help="file path when doing inference",
    )
    output_dir = os.environ.get("SM_OUTPUT_DATA_DIR", "submit")
    parser.add_argument(
        "--output_dir",
        dest="output_dir",
        default=output_dir,
        type=str,
        help="output directory",
    )

    parser = parser.parse_args()
    main(parser)
