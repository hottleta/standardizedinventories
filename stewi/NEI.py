#!/usr/bin/env python
"""
Imports NEI data and processes to Standardized EPA output format.
Uses the NEI data exports from EIS. Must contain locally downloaded data for
options A:E.
This file requires parameters be passed like:

    Option -y Year 

Options:
    A - for processing downloaded NEI Point from EIS
    B - for generating flowbyfacility output
    C - for generating flowbySCC output
    D - for generating flows output
    E - for generating facilities output
    F - for validating flowbyfacility against national totals

Year: 
    2017
    2016
    2014
    2011
"""

import stewi
from stewi.globals import set_dir,output_dir,data_dir,write_metadata,\
    get_relpath,unit_convert,log,\
    validate_inventory,write_validation_result,USton_kg,lb_kg,weighted_average, \
    storeParquet, config, compile_metadata
import pandas as pd
import numpy as np
import os
import argparse
import requests
import requests_ftp
import zipfile
import io

_config = config()['databases']['NEI']

nei_filepath = '../NEI/'
external_dir = set_dir(nei_filepath)
    
def read_data(year,file):
    """
    Reads the NEI data in the named file and returns a dataframe based on
    identified columns
    Parameters
    ----------
    year : str
        Year of NEI dataset for identifying field names
    file : str
        File name (csv) containing NEI data.

    Returns
    -------
    file_result : DataFrame
        DataFrame of NEI data from a single file with standardized column names.
    """
    file_result = pd.DataFrame(columns=list(nei_required_fields['StandardizedEPA']))
    # read nei file by chunks
    usecols = list(nei_required_fields[year].dropna())
    for file_chunk in pd.read_csv(
            external_dir + file,
            usecols=usecols,
            dtype={'sppd_facility_identifier':'str'},
            chunksize=100000,
            low_memory=False):
        # change column names to Standardized EPA names
        file_chunk = file_chunk.rename(columns=pd.Series(list(nei_required_fields['StandardizedEPA']),
                                                         index=list(nei_required_fields[year])).to_dict())
        # concatenate all chunks
        file_result = pd.concat([file_result,file_chunk])
    return file_result


def standardize_output(year, source='Point'):
    """
    Reads and parses NEI data
    Parameters
    ----------
    year : str
        Year of NEI dataset  

    Returns
    -------
    nei : DataFrame
        Dataframe of parsed NEI data.
    """
    # extract file paths
    file_paths = nei_file_path
    log.info('identified ' +str(len(file_paths)) + ' files: '+ ' '.join(file_paths))
    nei = pd.DataFrame()
    # read in nei files and concatenate all nei files into one dataframe
    for file in file_paths[0:]:
        # concatenate all other files
        log.info('reading NEI data from '+ file)
        nei = pd.concat([nei,read_data(year,file)])
        log.info(str(len(nei))+' records')
    # convert TON to KG
    nei['FlowAmount'] = nei['FlowAmount']*USton_kg

    log.info('adding Data Quality information')
    if source == 'Point':
        reliability_table = pd.read_csv(data_dir + 'DQ_Reliability_Scores_Table3-3fromERGreport.csv',
                                        usecols=['Source','Code','DQI Reliability Score'])
        nei_reliability_table = reliability_table[reliability_table['Source'] == 'NEI']
        nei_reliability_table['Code'] = nei_reliability_table['Code'].astype(float)
        nei['ReliabilityScore'] = nei['ReliabilityScore'].astype(float)
        nei = nei.merge(nei_reliability_table, left_on='ReliabilityScore', right_on='Code', how='left')
        nei['DataReliability'] = nei['DQI Reliability Score']
        # drop Code and DQI Reliability Score columns
        nei = nei.drop(['Code', 'DQI Reliability Score', 'ReliabilityScore'], 1)
    
        nei['Compartment']='air'
        '''
        # Modify compartment based on stack height (ft)
        nei.loc[nei['StackHeight'] < 32, 'Compartment'] = 'air/ground'
        nei.loc[(nei['StackHeight'] >= 32) & (nei['StackHeight'] < 164), 'Compartment'] = 'air/low'
        nei.loc[(nei['StackHeight'] >= 164) & (nei['StackHeight'] < 492), 'Compartment'] = 'air/high'
        nei.loc[nei['StackHeight'] >= 492, 'Compartment'] = 'air/very high'
        '''
    else:
        nei['DataReliability'] = 3
    # add Source column
    nei['Source'] = source
    return nei


def nei_aggregate_to_facility_level(nei_):
    """
    Aggregates NEI dataframe to flow by facility
    """
    # drops rows if flow amount or reliability score is zero
    nei_ = nei_[(nei_['FlowAmount'] > 0) & (nei_['DataReliability'] > 0)]

    grouping_vars = ['FacilityID', 'FlowName']
    neibyfacility = nei_.groupby(grouping_vars).agg({'FlowAmount': ['sum']})
    neibyfacility['DataReliability']=weighted_average(
        nei_, 'DataReliability', 'FlowAmount', grouping_vars)

    neibyfacility = neibyfacility.reset_index()
    neibyfacility.columns = neibyfacility.columns.droplevel(level=1)

    return neibyfacility

def nei_aggregate_to_custom_level(nei_, field):
    """
    Aggregates NEI dataframe to flow by facility by custom level (e.g. SCC)
    """
    # drops rows if flow amount or reliability score is zero
    nei_ = nei_[(nei_['FlowAmount'] > 0) & (nei_['DataReliability'] > 0)]

    grouping_vars = ['FacilityID', 'FlowName']
    if type(field) is str:
        grouping_vars.append(field)
    elif type(field) is list:
        grouping_vars.extend(field)
    neicustom = nei_.groupby(grouping_vars).agg({'FlowAmount': ['sum']})
    neicustom['DataReliability']=weighted_average(
        nei_, 'DataReliability', 'FlowAmount', grouping_vars)

    neicustom = neicustom.reset_index()
    neicustom.columns = neicustom.columns.droplevel(level=1)

    return neicustom


def generate_national_totals(year):
    """
    Downloads and parses pollutant national totals from 'Facility-level by
    Pollutant' data downloaded from EPA website. Used for validation.
    Creates NationalTotals.csv files.
    Parameters
    ----------
    year : str
        Year of NEI data for comparison.
    """
    log.info('Downloading national totals')
    
    ## generate url based on data year
    build_url = _config['national_url']
    version = _config['national_version'][year]
    url = build_url.replace('__year__', year)
    url = url.replace('__version__', version)
    
    ## make http request
    r = []
    requests_ftp.monkeypatch_session()
    try:
        r = requests.Session().get(url)
    except requests.exceptions.ConnectionError:
        log.error("URL Connection Error for " + url)
    try:
        r.raise_for_status()
    except requests.exceptions.HTTPError:
        log.error('Error in URL request!')
    
    ## extract data from zip archive
    z = zipfile.ZipFile(io.BytesIO(r.content))
    # create a list of files contained in the zip archive
    znames = z.namelist()
    # retain only those files that are in .csv format
    znames = [s for s in znames if '.csv' in s]
    # initialize the dataframe
    df = pd.DataFrame()
    # for all of the .csv data files in the .zip archive,
    # read the .csv files into a dataframe
    # and concatenate with the master dataframe
    # captures various column headings across years
    usecols = ['pollutant code','pollutant_cd',
               'pollutant desc','pollutant_desc', 'description',
               'total emissions','total_emissions',
               'emissions uom', 'uom'
               ]
    
    for i in range(len(znames)):
        headers = pd.read_csv(z.open(znames[i]),nrows=0)
        cols = [x for x in headers.columns if x in usecols]
        df = pd.concat([df, pd.read_csv(z.open(znames[i]), 
                                        usecols = cols)])    
    
    ## parse data
    # rename columns to match standard format
    df.columns = ['FlowID', 'FlowName', 'FlowAmount', 'UOM']
    # convert LB/TON to KG
    df['FlowAmount'] = np.where(df['UOM']=='LB',df['FlowAmount']*lb_kg,df['FlowAmount']*USton_kg)
    df = df.drop(['UOM'],1)
    # sum across all facilities to create national totals
    df = df.groupby(['FlowID','FlowName'])['FlowAmount'].sum().reset_index()
    # save national totals to .csv
    df.rename(columns={'FlowAmount':'FlowAmount[kg]'}, inplace=True)
    df.to_csv(data_dir+'NEI_'+year+'_NationalTotals.csv',index=False)

    return df


def generate_metadata(year):
    """
    Gets metadata and writes to .json
    """
    
    point_1_path = nei_filepath + nei_file_path[0]
    NEI_meta = compile_metadata(point_1_path, _config, year)

    #Write metadata to json
    write_metadata('NEI', year, NEI_meta)
    

if __name__ == '__main__':
    
    parser = argparse.ArgumentParser(argument_default = argparse.SUPPRESS)

    parser.add_argument('Option',
                        help = 'What do you want to do:\
                        [A] Process and pickle NEI data\
                        [B] Generate flowbyfacility output\
                        [C] Generate flows output\
                        [D] Generate facilities output\
                        [E] Validate flowbyfacility against national totals',
                        type = str)

    parser.add_argument('-y', '--Year', nargs = '+',
                        help = 'What NEI year you want to retrieve',
                        type = str)
    
    args = parser.parse_args()
    
    NEIyears = args.Year
    
    for year in NEIyears:
        if args.Option == 'A':

            nei_required_fields = pd.read_table(data_dir + 'NEI_required_fields.csv',sep=',')
            nei_required_fields = nei_required_fields[[year,'StandardizedEPA']]
            nei_file_path = _config[year]['file_name']

            nei_point = standardize_output(year)
            nei_point.to_pickle('work/NEI_' + year + '.pk')
            generate_metadata(year)
            
        elif args.Option == 'F':
            log.info('validating flow by facility against national totals')
            if not(os.path.exists(data_dir + 'NEI_'+ year + '_NationalTotals.csv')):
                generate_national_totals(year)
            else:
                log.info('using already processed national totals validation file')
            nei_national_totals = pd.read_csv(data_dir + 'NEI_'+ year + '_NationalTotals.csv',
                                              header=0,dtype={"FlowAmount[kg]":np.float})
            nei_flowbyfacility = stewi.getInventory('NEI', year, 'flowbyfacility',
                                                    filter_for_LCI = False,
                                                    US_States_Only = False)
            nei_flowbyfacility.drop(['Compartment'],1, inplace = True)
            nei_national_totals.rename(columns={'FlowAmount[kg]':'FlowAmount'},inplace=True)
            validation_result = validate_inventory(nei_flowbyfacility, nei_national_totals,
                                                   group_by='flow', tolerance=5.0)
            write_validation_result('NEI',year,validation_result)

        else:
            log.info('extracting data from NEI pickle')
            nei_point = pd.read_pickle('work/NEI_' + year + '.pk')
            
        if args.Option == 'B':
            log.info('generating flow by facility output')
            nei_point = nei_point.reset_index()
            nei_flowbyfacility = nei_aggregate_to_facility_level(nei_point)
            #nei_flowbyfacility.to_csv(output_dir+'flowbyfacility/NEI_'+year+'.csv',index=False)
            storeParquet(nei_flowbyfacility,'flowbyfacility/NEI_'+year)
            log.info(len(nei_flowbyfacility))
            #2017: 2184786
            #2016: 1965918
            #2014: 2057249
            #2011: 1840866

        elif args.Option == 'C':
            log.info('generating flow by SCC output')
            nei_point = nei_point.reset_index()
            nei_flowbySCC = nei_aggregate_to_custom_level(nei_point, 'SCC')
            #nei_flowbySCC.to_csv(output_dir+'flowbySCC/NEI_'+year+'.csv',index=False)
            storeParquet(nei_flowbySCC, 'flowbySCC/NEI_'+year)
            log.info(len(nei_flowbySCC))
            #2017: 4055707

        elif args.Option == 'D':
            log.info('generating flows output')
            nei_flows = nei_point[['FlowName', 'FlowID', 'Compartment']]
            nei_flows = nei_flows.drop_duplicates()
            nei_flows['Unit']='kg'
            nei_flows = nei_flows.sort_values(by='FlowName',axis=0)
            nei_flows.to_csv(output_dir+'flow/'+'NEI_'+year+'.csv',index=False)
            log.info(len(nei_flows))
            #2017: 293
            #2016: 282
            #2014: 279
            #2011: 277
            
        elif args.Option == 'E':
            log.info('generating facility output')
            facility = nei_point[['FacilityID', 'FacilityName', 'Address', 'City', 'State', 
                                  'Zip', 'Latitude', 'Longitude', 'NAICS', 'County']]
            facility = facility.drop_duplicates('FacilityID')
            facility.to_csv(output_dir+'facility/'+'NEI_'+year+'.csv',index=False)
            log.info(len(facility))
            #2017: 87162
            #2016: 85802
            #2014: 85125
            #2011: 95565
        
