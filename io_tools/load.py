## load packages
import numpy as np
import scipy
import pickle 
import os, sys, glob, time
from scipy.ndimage.interpolation import shift, map_coordinates

## Load other sub-packages
from .. import visual_tools, get_img_info, corrections, alignment_tools
from .. import _image_dtype
## Load shared parameters
from . import _distance_zxy, _image_size, _allowed_colors, _corr_channels, _correction_folder
from . import _num_buffer_frames, _num_empty_frames
from .crop import decide_starting_frames, translate_crop_by_drift


def get_num_frame(dax_filename, frame_per_color=_image_size[0], buffer_frame=10, empty_frame=0, verbose=False):
    """Function to extract image size and number of colors"""
    ## check input
    if '.dax' not in dax_filename:
        raise ValueError(
            f"Wrong input type, .dax file expected for {dax_filename}")
    if not os.path.isfile(dax_filename):
        raise IOError(f"input file:{dax_filename} doesn't exist!")

    _info_filename = dax_filename.replace('.dax', '.inf')
    with open(_info_filename, 'r') as _info_hd:
        _infos = _info_hd.readlines()
    # get frame number and color information
    _num_frame, _num_color = 0, 0
    _dx, _dy = 0, 0
    for _line in _infos:
        _line = _line.rstrip()
        if "number of frames" in _line:
            _num_frame = int(_line.split('=')[1])
            _num_color = (_num_frame - 2*buffer_frame - empty_frame) / frame_per_color
            if _num_color != int(_num_color):
                raise ValueError("Wrong num_color, should be integer!")
            _num_color = int(_num_color)
        if "frame dimensions" in _line:
            _dx = int(_line.split('=')[1].split('x')[0])
            _dy = int(_line.split('=')[1].split('x')[1])
    _im_shape = [_num_frame, _dx, _dy]

    return _im_shape, _num_color


# used by the old pipline
def multi_crop_image_fov(filename, channels, crop_limit_list,
                         all_channels=_allowed_colors, single_im_size=_image_size,
                         num_buffer_frames=10, num_empty_frames=0,
                         drift=np.array([0,0,0]), shift_order=1,
                         return_limits=False, verbose=False):
    """Function to load images for multiple cells in a fov
    Inputs:
        filename: .dax filename for given image, string of filename
        channels: color_channels for the specific data, list of int or str
        crop_limit_list: list of 2x2 or 3x2 array specifying where to crop, list of np.ndarray
        all_channels: all allowed colors in given data, list (default: _allowed_colors)
        single_im_size: image size for single color full image, list/array of 3 (default:[30,2048,2048])
        num_buffer_frame: number of frames before z-scan starts, int (default:10)
        num_empty_frames: number of empty frames at beginning of zscan, int (default: 0)
        drift: drift to ref-frame of this image, np.array of 3 (default:[0,0,0])
        return_limits: whether return drifted limits for cropping, bool (default: False)
        verbose: say something!, bool (default:False)
    Outputs:
        _cropped_im_list: cropped image list by crop_limit_list x channels
            list of len(crop_limit_list) x size of channels
        (optional) _drifted_limits: drifted list of crop limits
    """
    # load
    if 'DaxReader' not in locals():
        from ..visual_tools import DaxReader
    if 'get_num_frame' not in locals():
        from ..get_img_info import get_num_frame
    ## 0. Check inputs
    # filename
    if not os.path.isfile(filename):
        raise ValueError(f"file {filename} doesn't exist!")
    # channels 
    if isinstance(channels, list):
        _channels = [str(ch) for ch in channels]
    elif isinstance(channels, int) or isinstance(channels, str):
        _channels = [str(channels)]
    else:
        raise TypeError(f"Wrong input type for channels:{type(channels)}, should be list/str/int")
    # check channel values in all_channels
    for _ch in _channels:
        if _ch not in all_channels:
            raise ValueError(f"Wrong input for channel:{_ch}, should be among {all_channels}")
    # check num_buffer_frames and num_empty_frames
    num_buffer_frames = int(num_buffer_frames)
    num_empty_frames = int(num_empty_frames)

    ## 1. Load image
    if verbose:
        print(f"-- crop {len(crop_limit_list)} images with channels:{_channels}")
    # extract image info
    _full_im_shape, _num_channels = get_num_frame(filename,
                                                  frame_per_color=single_im_size[0],
                                                  buffer_frame=num_buffer_frames)
    # load the whole image
    if verbose:
        print(f"--- load image from file:{filename}", end=', ')
        _load_start = time.time()
    _full_im = DaxReader(filename, verbose=verbose).loadAll()
    # splice buffer frames
    _start_frames = decide_starting_frames(_channels, _num_channels, all_channels=all_channels,
                                           num_buffer_frames=num_buffer_frames, num_empty_frames=num_empty_frames,
                                           verbose=verbose)
    _splitted_ims = [_full_im[_sf:-num_buffer_frames:_num_channels] for _sf in _start_frames]
    if verbose:
        print(f"in {time.time()-_load_start}s")
    ## 2. Prepare crops
    if verbose:
        print(f"-- start cropping: ", end='')
        _start_time = time.time()
    _old_crop_list = []
    _drift_crop_list = []
    for _crop in crop_limit_list:
        if len(_crop) == 2:
            _n_crop = np.array([np.array([0, single_im_size[0]])]+list(_crop), dtype=np.int)
        elif len(_crop) == 3:
            _n_crop = np.array(_crop, dtype=np.int)
        else:
            raise ValueError(f"Wrong input _crop, should be 2d or 3d crop but {_crop} is given.")
        # append
        _old_crop_list.append(_n_crop)
        _drift_crop_list.append(translate_crop_by_drift(_n_crop, drift, single_im_size=single_im_size))
    # 2.1 Crop image
    _cropped_im_list = []
    _drifted_limits = []
    for _old_crop, _n_crop in zip(_old_crop_list, _drift_crop_list):
        _cims = []
        for _ch, _im in zip(_channels, _splitted_ims):
            if drift.any():
                # translate
                _cim = shift(_im, -drift, order=shift_order, mode='nearest')
            else:
                _cim = _im.copy()
            # revert to original crop size
            _diffs = (_old_crop - _n_crop).astype(np.int)
            _cims.append(_cim[_diffs[0, 0]: _diffs[0, 0]+_old_crop[0, 1]-_old_crop[0, 0],
                              _diffs[1, 0]: _diffs[1, 0]+_old_crop[1, 1]-_old_crop[1, 0],
                              _diffs[2, 0]: _diffs[2, 0]+_old_crop[2, 1]-_old_crop[2, 0]])
        # save cropped ims
        if isinstance(channels, list):
            _cropped_im_list.append(_cims)
        elif isinstance(channels, int) or isinstance(channels, str):
            _cropped_im_list.append(_cims[0])
        # save drifted limits
        _d_limits = np.array([_n_crop[:, 0]+_diffs[:, 0],
                              _n_crop[:, 0]+_diffs[:, 0]+_old_crop[:, 1]-_old_crop[:, 0]]).T
        _drifted_limits.append(_d_limits)
        if verbose:
            print("*", end='')
    
    if verbose:
        print(f"done in {time.time()-_start_time}s.")

    if return_limits:
        return _cropped_im_list, _drifted_limits
    else:
        return _cropped_im_list

# used by the new pipline
def correct_fov_image(dax_filename, sel_channels, 
                      load_file_lock=None,
                      single_im_size=_image_size, all_channels=_allowed_colors,
                      num_buffer_frames=10, num_empty_frames=0, 
                      drift=None, calculate_drift=False, 
                      drift_channel='488', ref_filename=None,  
                      use_autocorr=True, drift_args={},
                      corr_channels=_corr_channels, correction_folder=_correction_folder,
                      warp_image=True, 
                      hot_pixel_corr=True, hot_pixel_th=4, z_shift_corr=False,
                      illumination_corr=True, illumination_profile=None, 
                      bleed_corr=True, bleed_profile=None, 
                      chromatic_ref_channel='647', chromatic_corr=True, chromatic_profile=None, 
                      gaussian_highpass=False, gauss_sigma=3, gauss_truncate=2,
                      normalization=False, output_dtype=np.uint16,
                      return_drift=False, verbose=True):
    """Function to correct one whole field-of-view image in proper manner
    Inputs:
        
    Outputs:
        """
    ## check inputs
    # dax_filename
    if not os.path.isfile(dax_filename):
        raise IOError(f"Dax file: {dax_filename} is not a file, exit!")
    if not isinstance(dax_filename, str) or dax_filename[-4:] != '.dax':
        raise IOError(f"Dax file: {dax_filename} has wrong data type, exit!")
    if verbose:
        print(f"- correct the whole fov for image: {dax_filename}")
        _total_start = time.time()
    # selected channels
    if isinstance(sel_channels, str) or isinstance(sel_channels, int):
        sel_channels = [str(sel_channels)]
    else:
        sel_channels = [str(ch) for ch in sel_channels]
    # shared parameters
    single_im_size = np.array(single_im_size, dtype=np.int)
    all_channels = [str(ch) for ch in all_channels]
    num_buffer_frames = int(num_buffer_frames)
    num_empty_frames = int(num_empty_frames)
    # drift
    if drift is None:
        drift = np.zeros(len(single_im_size), dtype=np.float32)
    else:
        drift = np.array(drift, dtype=np.float32)
    if len(drift) != len(single_im_size):
        raise IndexError(f"drift should have the same dimension as single_im_size.")
    
    ## correction channels and profiles
    corr_channels = [str(ch) for ch in sorted(corr_channels, key=lambda v:-int(v)) if str(ch) in all_channels]    
    for _ch in corr_channels:
        if _ch not in all_channels:
            raise ValueError(f"Wrong correction channel:{_ch}, should be within {all_channels}")
    
    ## determine loaded channels
    _overlap_channels = [_ch for _ch in corr_channels if _ch in sel_channels] # channels needs be corrected by bleedthrough/chromatic
    if len(_overlap_channels) > 0 and bleed_corr:
        _load_channels = [_ch for _ch in corr_channels]
    else:
        _load_channels = []
    # append sel_channels
    for _ch in sel_channels:
        if _ch not in _load_channels:
            _load_channels.append(_ch)
    # append bead_image if going to do drift corr
    _drift_channel = str(drift_channel)
    if _drift_channel not in all_channels:
        raise ValueError(f"Wrong input of drift_channel:{_drift_channel}, should be among {all_channels}")
    if calculate_drift and drift_channel not in _load_channels:
        _load_channels.append(drift_channel)
    
    ## check profiles
    # load illumination profiles for selected channels (should exist for all include beads)
    if illumination_corr:
        if illumination_profile is None:
            illumination_profile = load_correction_profile('illumination', 
                                    corr_channels=_load_channels, 
                                    correction_folder=correction_folder, all_channels=all_channels,
                                    ref_channel=chromatic_ref_channel, 
                                    im_size=single_im_size, 
                                    verbose=verbose)
        else:
            if not isinstance(illumination_profile, dict):
                raise TypeError(f"Wrong input type of illumination_profile, should be dict!")
            for _ch in _load_channels:
                if _ch not in illumination_profile:
                    raise KeyError(f"channel:{_ch} not given in illumination_profile")
    # load bleedthrough profiles if required
    if bleed_corr and len(_overlap_channels) > 0:
        if bleed_profile is None:
            bleed_profile = load_correction_profile('bleedthrough', 
                                corr_channels=corr_channels, 
                                correction_folder=correction_folder, all_channels=all_channels,
                                ref_channel=chromatic_ref_channel, im_size=single_im_size, verbose=verbose)
        else:
            bleed_profile = np.array(bleed_profile, dtype=np.float32)
            if bleed_profile.shape != (len(corr_channels),len(corr_channels),single_im_size[-2], single_im_size[-1]) and bleed_profile.shape != tuple([len(corr_channels),len(corr_channels)]+list(single_im_size)):
                raise IndexError(f"Wrong input shape for bleed_profile: {bleed_profile.shape}, should be {(len(corr_channels),len(corr_channels),single_im_size[-2], single_im_size[-1])}")
    # load chromatic or chromatic_constants depends on whether do warpping
    if chromatic_corr and len(_overlap_channels) > 0:
        if chromatic_profile is None:
            if warp_image:
                chromatic_profile = load_correction_profile('chromatic', 
                                        corr_channels=corr_channels, 
                                        correction_folder=correction_folder, all_channels=all_channels,
                                        ref_channel=chromatic_ref_channel, 
                                        im_size=single_im_size, 
                                        verbose=verbose)
            else:
                chromatic_profile = load_correction_profile('chromatic_constants',      
                                        corr_channels=corr_channels, 
                                        correction_folder=correction_folder, all_channels=all_channels,
                                        ref_channel=chromatic_ref_channel, 
                                        im_size=single_im_size, 
                                        verbose=verbose)
        else:
            if not isinstance(chromatic_profile, dict):
                raise TypeError(f"Wrong input type of chromatic_profile, should be dict!")
            for _ch in _load_channels:
                if _ch in corr_channels and _ch not in chromatic_profile:
                    raise KeyError(f"channel:{_ch} not given in chromatic_profile")

    ## check output data-type
    # if normalization, output should be float
    if normalization and output_dtype==np.uint16:
        output_dtype = np.float32 
    # otherwise keep original dtype
    else:
        pass
    ## Load image
    if verbose:
        print(f"-- loading image from file:{dax_filename}", end=' ')
        _load_time = time.time()
    if 'DaxReader' not in locals():
        from ..visual_tools import DaxReader
    if 'load_file_lock' in locals() and load_file_lock is not None:
        load_file_lock.acquire()
    _reader = DaxReader(dax_filename, verbose=verbose)
    _raw_im = _reader.loadAll()
    _reader.close()
    if 'load_file_lock' in locals() and load_file_lock is not None:
        load_file_lock.release()
    # get number of colors and frames
    #from ..get_img_info import get_num_frame, split_channels
    _full_im_shape, _num_color = get_num_frame(dax_filename,
                                               frame_per_color=single_im_size[0],
                                               buffer_frame=num_buffer_frames, 
                                               empty_frame=num_empty_frames)
    _ims = split_im_by_channels(_raw_im, _load_channels, all_channels[:_num_color], 
                                single_im_size=single_im_size, 
                                num_buffer_frames=num_buffer_frames,
                                num_empty_frames=num_empty_frames, skip_frame0=False)
    # clear memory
    del(_raw_im)
    if verbose:
        print(f" in {time.time()-_load_time:.3f}s")

    ## hot-pixel removal
    if hot_pixel_corr:
        if verbose:
            print(f"-- removing hot pixels for channels:{_load_channels}", end=' ')
            _hot_time = time.time()
        # loop through and correct
        for _i, (_ch, _im) in enumerate(zip(_load_channels, _ims)):
            _nim = corrections.Remove_Hot_Pixels(_im.astype(np.float32),
                dtype=output_dtype, hot_th=hot_pixel_th)
            _ims[_i] = _nim
        if verbose:
            print(f"in {time.time()-_hot_time:.3f}s")

    ## Z-shift correction
    if z_shift_corr:
        if verbose:
            print(f"-- correct Z-shifts for channels:{_load_channels}", end=' ')
            _z_time = time.time()
        for _i, (_ch, _im) in enumerate(zip(_load_channels, _ims)):
            _ims[_i] = corrections.Z_Shift_Correction(_im.astype(np.float32),
                dtype=output_dtype, normalization=False)
        if verbose:
            print(f"in {time.time()-_z_time:.3f}s")
    ## bleedthrough correction
    # do bleedthrough correction if there's any final required image within corr_channels
    if len(_overlap_channels) > 0 and bleed_corr:
        if verbose:
            print(f"-- bleedthrough correction for channels: {corr_channels}", end=' ')
            _bleed_time = time.time()
        # extract all images within corr_channels
        _bld_ims = [_ims[_load_channels.index(_ch)] for _ch in corr_channels]
        # initialize list to store corrected images
        _bld_corr_ims = []
        for _i, _ch in enumerate(corr_channels):
            # new image is the sum of all intensity contribution from images in corr_channels
            _nim = np.sum([_im * bleed_profile[_i, _j] 
                            for _j,_im in enumerate(_bld_ims)],axis=0)
            _bld_corr_ims.append(_nim)
        # update images
        for _nim, _ch in zip(_bld_corr_ims, corr_channels):
            # restore output_type
            _nim[_nim > np.iinfo(output_dtype).max] = np.iinfo(output_dtype).max
            _nim[_nim < np.iinfo(output_dtype).min] = np.iinfo(output_dtype).min
            _ims[_load_channels.index(_ch)] = _nim.astype(output_dtype)
        # clear
        del(_bld_ims, _bld_corr_ims, bleed_profile)
        if verbose:
            print(f"in {time.time()-_bleed_time:.3f}s")

    ## illumination correction
    if illumination_corr:
        if verbose:
            print(f"-- illumination correction for channels:", end=' ')
            _illumination_time = time.time()
        for _i, (_im,_ch) in enumerate(zip(_ims, _load_channels)):
            if verbose:
                print(f"{_ch}", end=', ')
            _ims[_i] = (_im.astype(np.float32) / illumination_profile[_ch][np.newaxis,:]).astype(output_dtype)
        # clear
        del(illumination_profile)
        if verbose:
            print(f"in {time.time()-_illumination_time:.3f}s")

    ## calculate bead drift if required
    if calculate_drift:
        if verbose:
            print(f"-- apply bead_drift calculate for channel: {_drift_channel}")
            _drift_time = time.time()
        if 'align_image' not in locals():
            from ..correction_tools.alignment import align_image
        # update drift_args
        _updated_drift_args = {_k:_v for _k,_v in drift_args.items()}
        _updated_drift_args.update({
            'all_channels': all_channels,
            'ref_all_channels': all_channels,
            'drift_channel': drift_channel,
        })
        _drift_corr_args = {
            'single_im_size': single_im_size,
            'num_buffer_frames':num_buffer_frames,
            'num_empty_frames':num_empty_frames,
            'correction_folder':correction_folder,
        }
        _drift, _drift_flag = align_image(
            _ims[_load_channels.index(_drift_channel)],
            ref_filename, 
            use_autocorr=use_autocorr, 
            correction_args=_drift_corr_args,
            verbose=verbose,
            **_updated_drift_args,
        )
        
        if verbose:
            print(f"--- finish drift: {np.around(_drift,2)} in {time.time()-_drift_time:.3f}s")
    else:
        _drift = drift.copy()
        _drift_flag = 0
        
    ## chromatic abbrevation
    _chromatic_channels = [_ch for _ch in corr_channels 
                            if _ch in sel_channels and _ch != chromatic_ref_channel]
    if warp_image:
        if verbose:
            if chromatic_corr:
                print(f"-- warp image with chromatic correction for channels: {_chromatic_channels} and drift:{np.round(_drift, 2)}", end=' ')
            else:
                print(f"-- warp image with drift:{np.round(_drift, 2)}", end=' ')
            _warp_time = time.time()
        for _i, _ch in enumerate(sel_channels):
            if (chromatic_corr and _ch in _chromatic_channels) or _drift.any():
                if verbose:
                    print(f"{_ch}", end=', ')
                    # 0. get old image
                    _im = _ims[_load_channels.index(_ch)]
                    # 1. get coordiates to be mapped
                    _coords = np.meshgrid( np.arange(single_im_size[0]), 
                            np.arange(single_im_size[1]), 
                            np.arange(single_im_size[2]), )
                    # transpose is necessary  
                    _coords = np.stack(_coords).transpose((0, 2, 1, 3)) 
                    # 2. calculate corrected coordinates if chormatic abbrev.
                    if chromatic_corr and _ch in _chromatic_channels:
                        _coords = _coords + chromatic_profile[_ch]#[:,np.newaxis,:,:] # only need this for old correction
                    # 3. apply drift if necessary
                    if _drift.any():
                        _coords = _coords - _drift[:, np.newaxis,np.newaxis,np.newaxis]
                    # 4. map coordinates
                    _corr_im = map_coordinates(_im, 
                                               _coords.reshape(_coords.shape[0], -1),
                                               mode='nearest').astype(output_dtype)
                    _corr_im = _corr_im.reshape(np.shape(_im))
                    # append 
                    _ims[_load_channels.index(_ch)] = _corr_im.copy()
                    # local clear
                    del(_coords, _im, _corr_im)
        # clear
        if verbose:
            print(f"in {time.time()-_warp_time:.3f}s")
    else:
        if verbose:
            if chromatic_corr:
                print(f"-- generate translation function for chromatic correction for channels: {_chromatic_channels} and drift:{np.round(_drift, 2)}", end=' ')
            else:
                print(f"-- -- generate translation function with drift:{np.round(_drift, 2)}", end=' ')
            _warp_time = time.time()
        # generate mapping function for spot coordinates
        from ..correction_tools.chromatic import generate_chromatic_function
        # init corr functions
        _warp_functions = []
        for _i, _ch in enumerate(sel_channels):
            # with drift in consideration
            if (chromatic_corr and _ch in _chromatic_channels) or _drift.any():
                # with chromatic abbrevation
                if chromatic_corr and _ch in _chromatic_channels:
                    _func = generate_chromatic_function(chromatic_profile[_ch], _drift)
                # without chromatic
                else:
                    _func = generate_chromatic_function(None, _drift)
            # no translating
            else:
                def _func(_spots):
                    return _spots
            _warp_functions.append(_func)
        # clear
        if verbose:
            print(f"in {time.time()-_warp_time:.3f}s")
    ## apply gaussian
    if gaussian_highpass:
        if verbose:
            print(f"-- applying gaussian highpass filte, sigma={gauss_sigma}", end=' ')
            _highpass_time = time.time()
        from ..correction_tools.filter import gaussian_high_pass_filter
        for _i, _im in enumerate(_ims):
            _ims[_i] = gaussian_high_pass_filter(_im, gauss_sigma, gauss_truncate)
        # clear
        if verbose:
            print(f"in {time.time()-_highpass_time:.3f}s")

    ## normalization
    if normalization:
        for _i, _im in enumerate(_ims):
            _ims[_i] = _im.astype(np.float32) / np.median(_im)
    
    ## summarize and report selected_ims
    _sel_ims = []
    for _ch in sel_channels:
        _sel_ims.append(_ims[_load_channels.index(_ch)].astype(output_dtype).copy())
    # clear
    del(_ims)
    if verbose:
        print(f"-- finish correction in {time.time()-_total_start:.3f}s")
    # return
    _return_args = [_sel_ims]
    if not warp_image:
        _return_args.append(_warp_functions)
    if return_drift:
        _return_args.extend([_drift, _drift_flag])
    
    return tuple(_return_args)

# split multi-channel images from DNA-FISH
def split_im_by_channels(im, sel_channels, all_channels, single_im_size=_image_size,
                         num_buffer_frames=10, num_empty_frames=0, skip_frame0=False):
    """Function to split a full image by channels"""
    _num_colors = len(all_channels)
    if isinstance(sel_channels, str) or isinstance(sel_channels, int):
        sel_channels = [sel_channels]
    _sel_channels = [str(_ch) for _ch in sel_channels]
    if isinstance(all_channels, str) or isinstance(all_channels, int):
        all_channels = [all_channels]
    _all_channels = [str(_ch) for _ch in all_channels]
    for _ch in _sel_channels:
        if _ch not in _all_channels:
            raise ValueError(f"Wrong input channel:{_ch}, should be within {_all_channels}")
    _ch_inds = [_all_channels.index(_ch) for _ch in _sel_channels]
    _ch_starts = [num_empty_frames + num_buffer_frames \
                    + (_i - num_empty_frames - num_buffer_frames) %_num_colors 
                    for _i in _ch_inds]
    #print('_ch_inds', _ch_inds)
    #print('_ch_starts', _ch_starts)
    if skip_frame0:
        for _i,_s in enumerate(_ch_starts):
            if _s == _num_buffer_frames:
                _ch_starts[_i] += _num_colors

    _splitted_ims = [im[_s:_s+single_im_size[0]*_num_colors:_num_colors].copy() for _s in _ch_starts]

    return _splitted_ims


def load_correction_profile(corr_type, corr_channels=_corr_channels, 
                            correction_folder=_correction_folder, all_channels=_allowed_colors,
                            ref_channel='647', im_size=_image_size, verbose=False):
    """Function to load chromatic/illumination correction profile
    Inputs:
        corr_type: type of corrections to be loaded
        corr_channels: all correction channels to be loaded
    Outputs:
        _pf: correction profile, np.ndarray for bleedthrough, dict for illumination/chromatic
    """
    ## check inputs
    # type
    _allowed_types = ['chromatic', 'illumination', 'bleedthrough', 'chromatic_constants']
    _type = str(corr_type).lower()
    if _type not in _allowed_types:
        raise ValueError(f"Wrong input corr_type, should be one of {_allowed_types}")
    # channel
    _all_channels = [str(_ch) for _ch in all_channels]
    _corr_channels = [str(_ch) for _ch in corr_channels]
    for _channel in _corr_channels:
        if _channel not in _all_channels:
            raise ValueError(f"Wrong input channel:{_channel}, should be one of {_all_channels}")
    _ref_channel = str(ref_channel).lower()
    if _ref_channel not in _all_channels:
        raise ValueError(f"Wrong input ref_channel:{_ref_channel}, should be one of {_all_channels}")

    ## start loading file
    if verbose:
        print(f"-- loading {_type} correction profile from file", end=':')
    if _type == 'bleedthrough':
        _basename = _type+'_correction' \
            + '_' + '_'.join(sorted(_corr_channels, key=lambda v:-int(v))) \
            + '_' + str(im_size[-2])+'_'+str(im_size[-1])+'.npy'
        if verbose:
            print(_basename)
        _pf = np.load(os.path.join(correction_folder, _basename), allow_pickle=True)
        _pf = _pf.reshape(len(_corr_channels), len(_corr_channels), im_size[-2], im_size[-1])
    elif _type == 'chromatic':
        if verbose:
            print('')
        _pf = {}
        for _channel in _corr_channels:
            if _channel != _ref_channel:
                _basename = _type+'_correction' \
                + '_' + str(_channel) + '_' + str(_ref_channel)
                for _d in im_size:
                    _basename += f'_{int(_d)}'
                _basename += '.npy'

                if verbose:
                    print('\t',_channel,_basename)
                _pf[_channel] = np.load(os.path.join(correction_folder, _basename), allow_pickle=True)
            else:
                if verbose:
                    print('\t',_channel, None)
                _pf[_channel] = None
    elif _type == 'chromatic_constants':
        if verbose:
            print('')
        _pf = {}
        for _channel in _corr_channels:
            if _channel != _ref_channel:
                _basename = _type.split('_')[0]+'_correction' \
                + '_' + str(_channel) + '_' + str(_ref_channel) 
                for _d in im_size:
                    _basename += f'_{int(_d)}'
                _basename += '_const.pkl'
                
                if verbose:
                    print('\t',_channel,_basename)
                _pf[_channel] = pickle.load(open(os.path.join(correction_folder, _basename), 'rb') )
            else:
                if verbose:
                    print('\t',_channel, None)
                _pf[_channel] = None
    elif _type == 'illumination':
        if verbose:
            print('')
        _pf = {}
        for _channel in _corr_channels:
            _basename = _type+'_correction' \
            + '_' + str(_channel) \
            + '_' + str(im_size[-2])+'x'+str(im_size[-1])+'.npy'
            if verbose:
                print('\t',_channel,_basename)
            _pf[_channel] = np.load(os.path.join(correction_folder, _basename), allow_pickle=True)

    return _pf 

def find_image_background(im, dtype=_image_dtype, bin_size=10, make_plot=False, max_iter=10):
    """Function to calculate image background
    Inputs: 
        im: image, np.ndarray,
        dtype: data type for image, numpy datatype (default: np.uint16) 
        bin_size: size of histogram bin, smaller -> higher precision and longer time,
            float (default: 10)
    Output: 
        _background: determined background level, float
    """
    
    if dtype is None:
        dtype = im.dtype 
    _cts, _bins = np.histogram(im, 
                               bins=np.arange(np.iinfo(dtype).min, 
                                              np.iinfo(dtype).max,
                                              bin_size)
                               )
    _peaks = []
    # gradually lower height to find at least one peak
    _height = np.size(im)/50
    _iter = 0
    while len(_peaks) == 0:
        _height = _height / 2 
        _peaks, _params = scipy.signal.find_peaks(_cts, height=_height)
        _iter += 1
        if _iter > max_iter:
            break
    # select highest peak
    if _iter > max_iter:
        _background = np.nanmedian(im)
    else:
        _sel_peak = _peaks[np.argmax(_params['peak_heights'])]
        # define background as this value
        _background = (_bins[_sel_peak] + _bins[_sel_peak+1]) / 2
    # plot histogram if necessary
    if make_plot:
        import matplotlib.pyplot as plt
        plt.figure(dpi=100)
        plt.hist(np.ravel(im), bins=np.arange(np.iinfo(dtype).min, 
                                              np.iinfo(dtype).max,
                                              bin_size))
        plt.xlim([np.min(im), np.max(im)])     
        plt.show()

    return _background