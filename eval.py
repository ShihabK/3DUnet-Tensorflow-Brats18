# -*- coding: utf-8 -*-
# File: eval.py

import tqdm
import os
from collections import namedtuple
import numpy as np
import cv2

from tensorpack.utils.utils import get_tqdm_kwargs
import config
from utils import *

def batch_segmentation(temp_imgs, model_func, data_shape=[19, 180, 160]):
    batch_size = config.BATCH_SIZE
    data_channel = 4
    class_num = config.NUM_CLASS
    image_shape = temp_imgs[0].shape
    label_shape = [data_shape[0], data_shape[1], data_shape[2]]
    D, H, W = image_shape
    input_center = [int(D/2), int(H/2), int(W/2)]
    temp_prob1 = np.zeros([D, H, W, class_num])

    sub_image_batches = []
    for center_slice in range(int(label_shape[0]/2), D + int(label_shape[0]/2), label_shape[0]):
        center_slice = min(center_slice, D - int(label_shape[0]/2))
        sub_image_batch = []
        for chn in range(data_channel):
            temp_input_center = [center_slice, input_center[1], input_center[2]]
            sub_image = extract_roi_from_volume(
                            temp_imgs[chn], temp_input_center, data_shape, fill="zero")
            sub_image_batch.append(sub_image)
        sub_image_batch = np.asanyarray(sub_image_batch, np.float32) #[4,180,160]
        sub_image_batches.append(sub_image_batch) # [14,4,d,h,w]
    
    total_batch = len(sub_image_batches)
    max_mini_batch = int((total_batch+batch_size-1)/batch_size)
    sub_label_idx1 = 0
    for mini_batch_idx in range(max_mini_batch):
        data_mini_batch = sub_image_batches[mini_batch_idx*batch_size:
                                      min((mini_batch_idx+1)*batch_size, total_batch)]
        if(mini_batch_idx == max_mini_batch - 1):
            for idx in range(batch_size - (total_batch - mini_batch_idx*batch_size)):
                data_mini_batch.append(np.zeros([data_channel] + data_shape))
                # data_mini_batch.append(np.random.normal(0, 1, size = [data_channel] + data_shape))
        data_mini_batch = np.asarray(data_mini_batch, np.float32)
        data_mini_batch = np.transpose(data_mini_batch, [0, 2, 3, 4, 1])
        prob_mini_batch1, _ = model_func(data_mini_batch)
        
        for batch_idx in range(prob_mini_batch1.shape[0]):
            center_slice = sub_label_idx1*label_shape[0] + int(label_shape[0]/2)
            center_slice = min(center_slice, D - int(label_shape[0]/2))
            temp_input_center = [center_slice, input_center[1], input_center[2], int(class_num/2)]
            sub_prob = np.reshape(prob_mini_batch1[batch_idx], label_shape + [class_num])
            temp_prob1 = set_roi_to_volume(temp_prob1, temp_input_center, sub_prob)
            sub_label_idx1 = sub_label_idx1 + 1
    
    return temp_prob1

def segment_one_image_dynamic(data, create_model_func):
    """
    Change PATCH_SIZE in inference if cropped brain region > PATCH_SIZE
    NOTE: After testing, this function makes little difference 
            compared to setting larger patch_size at first place.
    """
    def get_dynamic_shape(image_shape):
        [D, H, W] = image_shape
        data_shape = config.PATCH_SIZE
        Hx = max(int((H+3)/4)*4, data_shape[1])
        Wx = max(int((W+3)/4)*4, data_shape[2])
        data_slice = data_shape[0]
        label_slice = data_shape[0]
        full_data_shape = [data_slice, Hx, Wx]
        return full_data_shape

    img = data['images']
    temp_weight = data['weights'][:,:,:,0]
    temp_size = data['original_shape']
    temp_bbox = data['bbox']
    struct = ndimage.generate_binary_structure(3, 2)
    margin = 5
    wt_threshold = 2000
    
    img = img[np.newaxis, ...] # add batch dim

    im = img
    im_ax = np.transpose(im[0], [3, 0 ,1, 2]) # mod, d, h, w
    im_ax = transpose_volumes(im_ax, 'axial')
    [D, H, W] = im_ax.shape
    if not (H <= config.PATCH_SIZE[1] and W <= config.PATCH_SIZE[2]):
        full_data_shape = get_dynamic_shape(im_ax[0].shape)
        dy_model_func = create_model_func[0](full_data_shape)
        prob1_ax = batch_segmentation(im_ax, dy_model_func, data_shape=full_data_shape)
    else:
        dy_model_func = create_model_func[0](config.PATCH_SIZE)
        prob1_ax = batch_segmentation(im_ax, dy_model_func, data_shape=config.PATCH_SIZE)
    # need to take care if image size > data_shape

    if config.MULTI_VIEW:
        im_sa = np.transpose(im[0], [3, 0 ,1, 2]) # mod, d, h, w
        im_sa = transpose_volumes(im_sa, 'sagittal')
        [D, H, W] = im_sa.shape
        if not (H <= config.PATCH_SIZE[1] and W <= config.PATCH_SIZE[2]):
            full_data_shape = get_dynamic_shape(im_sa.shape)
            dy_model_func = create_model_func[1](full_data_shape)
            prob1_sa = batch_segmentation(im_sa, dy_model_func, data_shape=full_data_shape)
        else:
            dy_model_func = create_model_func[1](config.PATCH_SIZE)
            prob1_sa = batch_segmentation(im_sa, dy_model_func, data_shape=config.PATCH_SIZE)

        im_co = np.transpose(im[0], [3, 0 ,1, 2]) # mod, d, h, w
        im_co = transpose_volumes(im_co, 'coronal')
        [D, H, W] = im_co.shape
        if not (H <= config.PATCH_SIZE[1] and W <= config.PATCH_SIZE[2]):
            full_data_shape = get_dynamic_shape(im_co.shape)
            dy_model_func = create_model_func[2](full_data_shape)
            prob1_co = batch_segmentation(im_co, dy_model_func, data_shape=full_data_shape)
        else:
            dy_model_func = create_model_func[2](config.PATCH_SIZE)
            prob1_co = batch_segmentation(im_co, dy_model_func, data_shape=config.PATCH_SIZE)

        pred1 = (prob1_ax + np.transpose(prob1_sa, (1,2,0,3)) + np.transpose(prob1_co, (1,0,2,3)))/ 3.0
        pred1 = np.argmax(pred1, axis=-1)
    else:
        pred1 = np.argmax(prob1_ax, axis=-1)
    
    pred1 = pred1 * temp_weight # clear non-brain region
    # pred1 should be the same as cropped brain region
    # now fill the croped region with our prediction
    out_label = np.asarray(pred1, np.int16)
    final_label = np.zeros(temp_size, np.int16)
    final_label = set_ND_volume_roi_with_bounding_box_range(final_label, temp_bbox[0], temp_bbox[1], out_label)
    final_probs = prob1_ax # no-use for now
        
    return final_label, final_probs

def segment_one_image(data, model_func):
    """
    Run detection on one image, using the TF callable.
    This function should handle the preprocessing internally.

    Args:
        img: an image
        model_func: a callable from TF model,
            takes image and returns (boxes, probs, labels, [masks])

    Returns:
        [DetectionResult]
    """
    img = data['images']
    temp_weight = data['weights'][:,:,:,0]
    temp_size = data['original_shape']
    temp_bbox = data['bbox']
    struct = ndimage.generate_binary_structure(3, 2)
    margin = 5
    wt_threshold = 2000
    
    img = img[np.newaxis, ...] # add batch dim

    im = img
    im_ax = np.transpose(im[0], [3, 0 ,1, 2]) # mod, d, h, w
    im_ax = transpose_volumes(im_ax, 'axial')
    prob1_ax = batch_segmentation(im_ax, model_func[0], data_shape=config.PATCH_SIZE)
    # need to take care if image size > data_shape

    if config.MULTI_VIEW:
        im_sa = np.transpose(im[0], [3, 0 ,1, 2]) # mod, d, h, w
        im_sa = transpose_volumes(im_sa, 'sagittal')
        prob1_sa = batch_segmentation(im_sa, model_func[1], data_shape=[20,240,240])

        im_co = np.transpose(im[0], [3, 0 ,1, 2]) # mod, d, h, w
        im_co = transpose_volumes(im_co, 'coronal')
        prob1_co = batch_segmentation(im_co, model_func[2], data_shape=[20,240,240])

        pred1 = (prob1_ax + np.transpose(prob1_sa, (1,2,0,3)) + np.transpose(prob1_co, (1,0,2,3)))/ 3.0
        pred1 = np.argmax(pred1, axis=-1)
    else:
        pred1 = np.argmax(prob1_ax, axis=-1)
    
    pred1 = pred1 * temp_weight # clear non-brain region
    # pred1 should be the same as cropped brain region
    # now fill the croped region with our prediction
    out_label = np.asarray(pred1, np.int16)
    final_label = np.zeros(temp_size, np.int16)
    final_label = set_ND_volume_roi_with_bounding_box_range(final_label, temp_bbox[0], temp_bbox[1], out_label)
    final_probs = prob1_ax # no-use for now
        
    return final_label, final_probs

def dice_of_brats_data_set(gt, pred, type_idx):
    dice_all_data = []
    for i in range(len(gt)):
        g_volume = gt[i]
        s_volume = pred[i]
        dice_one_volume = []
        if(type_idx ==0): # whole tumor
            temp_dice = binary_dice3d(s_volume > 0, g_volume > 0)
            dice_one_volume = [temp_dice]
        elif(type_idx == 1): # tumor core
            s_volume[s_volume == 2] = 0
            g_volume[g_volume == 2] = 0
            temp_dice = binary_dice3d(s_volume > 0, g_volume > 0)
            dice_one_volume = [temp_dice]
        else:
            #for label in [1, 2, 3, 4]: # dice of each class
            temp_dice = binary_dice3d(s_volume == 4, g_volume == 4)
            dice_one_volume = [temp_dice]
        dice_all_data.append(dice_one_volume)
    return dice_all_data

def eval_brats(df, detect_func, with_gt=True):
    """
    Args:
        df: a DataFlow which produces (image, image_id)
        detect_func: a callable, takes [image] and returns [DetectionResult]

    Returns:
        list of dict, to be dumped to COCO json format
    """
    df.reset_state()
    gts = []
    results = []
    with tqdm.tqdm(total=df.size(), **get_tqdm_kwargs()) as pbar:
        for filename, image_id, data in df.get_data():
            pred, probs = detect_func(data)
            gt = load_nifty_volume_as_array("{}/{}_seg.nii.gz".format(filename, image_id))
            gts.append(gt)
            results.append(pred)
            pbar.update()
    test_types = ['whole', 'core', 'enhancing']
    ret = {}
    for type_idx in range(3):
        dice = dice_of_brats_data_set(gts, results, type_idx)
        dice = np.asarray(dice)
        dice_mean = dice.mean(axis = 0)
        dice_std  = dice.std(axis = 0)
        test_type = test_types[type_idx]
        ret[test_type] = dice_mean[0]
        print('tissue type', test_type)
        print('dice mean', dice_mean)
    return ret

def pred_brats(df, detect_func):
    """
    Args:
        df: a DataFlow which produces (image, image_id)
        detect_func: a callable, takes [image] and returns [DetectionResult]

    Returns:
        list of dict, to be dumped to COCO json format
    """
    df.reset_state()
    gts = []
    results = []
    with tqdm.tqdm(total=df.size(), **get_tqdm_kwargs()) as pbar:
        for filename, image_id, data in df.get_data():
            pred, probs = detect_func(data)
            save_to_nii(pred, image_id, outdir="eval_out18", mode="label")
            # save prob to ensemble
            # save_to_pkl(probs, image_id, outdir="eval_out18_prob_{}".format(config.CROSS_VALIDATION))
            pbar.update()
    return None
 