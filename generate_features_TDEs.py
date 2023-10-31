import requests
import numpy as np
import glob
import pandas as pd
from io import BytesIO
import sys
import os
import tools_from_sn_classifier as sn_tools


def get_data_from_FINK(save = True, extended = False):

	# Get object names
	TDEs_hammerstein = pd.read_fwf('ZTF_TDE_Data/Table1_Hammerstein', skiprows = 34, header = None)
	ztf_names = TDEs_hammerstein[1].to_list()

	what_to_retrieve = ['i:jd', 'i:fid', 'i:magpsf', 'i:sigmapsf', 'i:candid', 'i:objectId',
					 'i:ra', 'i:dec']
	if extended:
		extra_cols = ['d:snn_sn_vs_all', 'd:snn_snia_vs_nonia']
		what_to_retrieve = what_to_retrieve + extra_cols

	r = requests.post('https://fink-portal.org/api/v1/objects',
	  json={
		  'objectId': ','.join(ztf_names),
		  'columns': ','.join(what_to_retrieve)
	  }
	)
	fink_df = pd.read_json(BytesIO(r.content))

	fink_df.columns = fink_df.columns.str.lstrip('i:')  # strip prefix
	# fink_df.columns = fink_df.columns.str.lstrip('d:')  # strip prefix

	if save:
		fink_df.to_csv('ZTF_TDE_Data/from_Fink.csv', index = None)

	tdes_not_in_fink_data = [x for x in ztf_names if x not in fink_df.objectId.unique()]
	return fink_df, tdes_not_in_fink_data


def load_forced_photometry_data(fink_df, quality_cuts = True, SNT_thresh = 3):
	"""
	Load forced photometry data, and crossmatch with Fink data positions.
	Conversion of some columns to be like fink data.

	Parameters
	----------
	fink_df : TYPE
		DESCRIPTION.

	Returns
	-------
	all_objects_df : TYPE
		DESCRIPTION.

	"""

	# Load forced-photometry data
	forced_phot_data = glob.glob('ZTF_TDE_Data/forced_photometry/batchfp_*.txt')
	list_of_dfs = []
	for forced_phot_fname in forced_phot_data:
		obj_id = find_objectId_for_forced_phot_data(forced_phot_fname, fink_df)
		df_fp = pd.read_csv(forced_phot_fname, comment = '#', sep = ' ')
		df_fp.columns = df_fp.columns.str.strip(',')  # strip prefix
		df_fp['objectId'] = obj_id
		df_fp['fp_fname'] = os.path.basename(forced_phot_fname)
		list_of_dfs.append(df_fp)

	all_objects_df = pd.concat(list_of_dfs)

	all_objects_df.dropna(subset=['objectId'], inplace=True)

	if quality_cuts:
		all_objects_df = all_objects_df[(all_objects_df['infobitssci'] == 0)
		& (all_objects_df['forcediffimflux'] / all_objects_df['forcediffimfluxunc'] > SNT_thresh)]
	# all_objects_df = all_objects_df[all_objects_df['forcediffimflux'] > (-1000)]

	return all_objects_df


def diff_phot(forcediffimflux, forcediffimfluxunc, zpdiff, SNT=3, SNU=5, set_to_nan=True):
    """
	Get magpsf and sigmapsf from forced photometry parameters. Function provided by Julien.
    """
    if (forcediffimflux / forcediffimfluxunc) > SNT:
        # we have a confident detection, compute and plot mag with error bar:
        mag = zpdiff - 2.5 * np.log10(forcediffimflux)
        err = 1.0857 * forcediffimfluxunc / forcediffimflux
    else:
        # compute flux upper limit and plot as arrow:
        if not set_to_nan:
            mag = zpdiff - 2.5 * np.log10(SNU * forcediffimfluxunc)
        else:
            mag = np.nan
        err = np.nan

    return mag, err


def merge_features_tdes_SN(csv_tdes, csv_other, out_csv):
	"""
	Merges the new features obtained for TDEs with those from the SN study.

	Parameters
	----------
	csv_tdes : TYPE
		DESCRIPTION.
	csv_other : TYPE
		DESCRIPTION.
	out_csv : TYPE
		DESCRIPTION.

	Returns
	-------
	None.

	"""

	feat_tdes = pd.read_csv(csv_tdes)
	feat_other = pd.read_csv(csv_other)

	merged_df = pd.concat([feat_tdes, feat_other])

	merged_df.to_csv(out_csv, index = False)


def crop_lc_to_rsing_part(converted_df: pd.DataFrame, minimum_nb_obs: int = 3, save_csv = True):
	"""
	Crop the light-curve to retain only the rising part. Drop every observation after the max flux.
	Keep observations of an object only if the object presents at least "minimum_nb_obs" observations.
	# TODO: Maybe trim based on histogram (get e.g. 90 pr cent)

	Parameters
	----------
	converted_df : pd.DataFrame
		Dataset with columns ['objectId', 'type', 'MJD', 'FLT','FLUXCAL', 'FLUXCALERR'].
	minimum_nb_obs : int, optional
		Minimum number of observations (otherwise drop alerts of object). The default is 3.
	save_csv : bool, optional
		Wehther to save the output dataframe onto a csv. The default is True.

	Returns
	-------
	converted_df_early :  pd.DataFrame
		Data prepared for the fitting, after dropping the decaying part of the LC.

	"""

	df_list = []
	for indx in range(np.unique(converted_df['id'].values).shape[0]):

		name = np.unique(converted_df['id'].values)[indx]
		obj_flag = converted_df['id'].values == name
		obj_df = converted_df[obj_flag]

		for filt in ['g', 'r']:
			object_df = obj_df[obj_df['FLT'] == filt].copy()
			if len(object_df) > minimum_nb_obs:
				tmax = object_df['MJD'][object_df['FLUXCAL'].idxmax()]
				object_df = object_df[object_df.MJD <= tmax]
				df_list.append(object_df)

	converted_df_early = pd.concat(df_list)
	if save_csv:
		converted_df_early.to_csv('input_for_feature_extractor.csv', index = False)
	return converted_df_early


def crop_lc_based_on_csv_values(df):


	# Hard-code csv filename and read
	csv_fname = 'ZTF_TDE_Data/forced_photometry/TimeParametersTDEs_training.csv'
	times_csv = pd.read_csv(csv_fname)

	# Crossmatch csvs and cut LCs
	merged_csv = df.merge(times_csv, left_on = 'fp_fname', right_on = 'Filename')

	converted_df_early = df[(merged_csv.MJD >= merged_csv['Start (MJD)'] + 2400000.5)
						  & (merged_csv.MJD <= merged_csv['Peak (MJD)'] + 2400000.5)]
	return converted_df_early



def is_unique(s):
	""" Check if all values of a (pandas) series are equal. """
	a = s.to_numpy()
	return (a[0] == a).all()


def find_objectId_for_forced_phot_data(forced_phot_fname, df_fink, deg_tolerance = 0.001):
	"""
	Correlate the forced photometry data with the Fink data, to find the object ID corresponding
	to the forced-photometry data file.

	Parameters
	----------
	forced_phot_fname : str
		filename containing the forced_phot data of one object.
	df_fink : pd.DataFrame
		Data from Fink with all the objects at interest.
	deg_tolerance : float, optional
		Margin in degrees, for the search in RA and DEC. The default is 0.001.

	Returns
	-------
	obj_id : str
		Object Identifier.

	"""

	with open(forced_phot_fname) as f:
	    for i, line in enumerate(f):
	        if i == 3:
	            req_ra = float(line.split(' ')[-2])
	        elif i ==4:
	            req_dec = float(line.split(' ')[-2])
	        elif i > 4:
	            break

	# TODO: do it with astropy
	df_obj = df_fink[(df_fink.ra > req_ra - deg_tolerance) & (df_fink.ra < req_ra + deg_tolerance) &
			 (df_fink.dec > req_dec - deg_tolerance) & (df_fink.dec < req_dec + deg_tolerance)]
	if len(df_obj) == 0:
		print('Error while correlating the forced-phometry data with the objectId: '+
				'No object was found in this position, for ' + forced_phot_fname)

		obj_id = os.path.basename(forced_phot_fname)

	elif is_unique(df_obj.objectId):
		obj_id = df_obj.objectId.iloc[0]

	else:
		print('Error while correlating the forced-phometry data with the objectId: '+
				'more than one object are within the given position given.')
		obj_id = None
	return obj_id


def convert_forced_phot_df(df):

# 	df.rename(columns = {'forcediffimflux': 'FLUXCAL',
# 					  'forcediffimfluxunc': 'FLUXCALERR',
	df.rename(columns = { 'objectId' : 'id',
					  'jd': 'MJD'}, inplace = True)
	df['magpsf'] = df['zpdiff'] - 2.5 * np.log10(df['forcediffimflux'])
	df['FLUXCAL'] = 10 ** (-0.4 * df['magpsf']) * 10 ** (11)

	df['sigmapsf'] = 1.0857 * df['forcediffimfluxunc'] / df['forcediffimflux']
	df['FLUXCALERR'] =  9.21034 * 10 ** 10 * np.exp(-0.921034 * df['magpsf']) * df['sigmapsf']


	df['FLT']  = df['filter'].str[-1]
	df['type'] = 'TDE'
	df.reset_index(inplace = True)

	return df[['id', 'type', 'MJD', 'FLT', 'FLUXCAL', 'FLUXCALERR', 'fp_fname']]


def convert_df(fink_df, data_origin = 'fink'):


	if data_origin =='forced_phot':
		df = load_forced_photometry_data(fink_df)
		converted_df = convert_forced_phot_df(df)
	elif data_origin == 'fink':
		converted_df = sn_tools.convert_full_dataset(fink_df, obj_id_header='objectId')
	else:
		print('wrong string given')
		sys.exit()
	return converted_df


if __name__ == '__main__':

	data_origin = 'forced_phot'
	# Get data and prepare for fitting
	fink_df,_ = get_data_from_FINK(save = True, extended = True)

	fink_df = pd.read_csv('ZTF_TDE_Data/from_Fink.csv')
	converted_df = convert_df(fink_df, data_origin)

	if data_origin == 'fink':
		converted_df_early = crop_lc_to_rsing_part(converted_df)
	elif data_origin =='forced_phot':
		converted_df_early = crop_lc_based_on_csv_values(converted_df)
	else:
		print('wrong string given')
		sys.exit()

	# converted_df_early = converted_df
	converted_df_early.to_csv('data_for_feat_extractor.csv', index = False)

# 	# Obtain features and save
	feature_matrix = sn_tools.featurize_full_dataset(converted_df_early, screen = True)
	feature_matrix.to_csv('Features_check/features_tdes.csv', index = None)
	merge_features_tdes_SN('Features_check/features_tdes.csv', 'Features_check/features.csv',
						   'Features_check/merged_features.csv')


