"""
Functions to combine inventory data
"""
import facilitymatcher
from stewicombo.overlaphandler import aggregate_and_remove_overlap
from stewicombo.globals import get_id_before_underscore, getInventoriesforFacilityMatches,\
    addChemicalMatches, addBaseInventoryIDs


def combineFullInventories(inventory_dict, filter_for_LCI=True, 
                           remove_overlap=True, compartments=None):
    """Combines full stewi inventories

    :param inventory_dict: dictionary of inventories and years,
     e.g. {"TRI":"2014","NEI":"2014","RCRAInfo":"2015"}
    :param filter_for_LCI: boolean. Passes through to stewi to filter_for_LCI. See stewi.
    :param remove_overlap: boolean. Removes overlap across inventories based on preferences
     defined in globals
    :param compartments: list of compartments to include (e.g., 'water', 'air', 'land')
    :return: Flow-By-Facility Combined Format
    """

    inventory_acronyms = list(inventory_dict.keys())
    facilitymatches = facilitymatcher.get_matches_for_inventories(inventory_acronyms)
    inventories = getInventoriesforFacilityMatches(inventory_dict, facilitymatches, filter_for_LCI)
    
    # filter by compartment
    if compartments !=None:
        #TODO disaggregate compartments to include all children
        inventories = inventories[inventories['Compartment'].isin(compartments)]
    
    inventories = addChemicalMatches(inventories)
   
    # Aggregate and remove overlap if requested
    if remove_overlap:
        inventories = aggregate_and_remove_overlap(inventories)
        # For combined records, preserve record of that in 'FacilityIDs_Combined'
        inventories['FacilityIDs_Combined'] = inventories['FacilityID']
        # Otherwise take the first ID as the facility ID
        inventories['FacilityID'] = \
            inventories['FacilityID'].apply(lambda x: get_id_before_underscore(x))

    return inventories


def combineInventoriesforFacilitiesinOneInventory(base_inventory, inventory_dict,
                                                  filter_for_LCI=True, remove_overlap=True):
    """Combines stewi inventories for all facilities present in a given base_inventory inventory

    The base_inventory must be in the inventory_dict
    :param base_inventory: reference inventory e.g. "TRI"
    :param inventory_dict: dictionary of inventories and years,
     e.g. {"TRI":"2014","NEI":"2014","RCRAInfo":"2015"}
    :param filter_for_LCI: boolean. Passes through to stewi to filter_for_LCI. See stewi.
    :param remove_overlap: boolean.
     Removes overlap across inventories based on preferences defined in globals
    :return: Flow-By-Facility Combined Format
    """

    inventory_acronyms = list(inventory_dict.keys())
    facilitymatches = facilitymatcher.get_matches_for_inventories(inventory_acronyms)
    inventories = getInventoriesforFacilityMatches(inventory_dict, facilitymatches,
                                                   filter_for_LCI, base_inventory)
    inventories = addChemicalMatches(inventories)

    # Aggregate and remove overlap if requested
    if remove_overlap:
        inventories = aggregate_and_remove_overlap(inventories)

    inventories = addBaseInventoryIDs(inventories, facilitymatches, base_inventory)
    return inventories


def combineInventoriesforFacilityList(base_inventory, inventory_dict, facility_id_list,
                                      filter_for_LCI=True, remove_overlap=True):
    """Combines stewi inventories for all facilities present in a given facility id list

    The base_inventory must be in the inventory_dict
    :param base_inventory: reference inventory e.g. "TRI"
    :param inventory_dict: dictionary of inventories and years,
     e.g. {"TRI":"2014","NEI":"2014","RCRAInfo":"2015"}
    :param facility_id_list: list of facility ids from base_inventory
     e.g. ['99501MPCLS1076O', '99501NCHRG459WB', '99515VNWTR590E1']
    :param filter_for_LCI: boolean. Passes through to stewi to filter_for_LCI. See stewi.
    :param remove_overlap: boolean.
     Removes overlap across inventories based on preferences defined in globals
    :return: Flow-By-Facility Combined Format
    """

    inventory_acronyms = list(inventory_dict.keys())
    facilitymatches = facilitymatcher.get_matches_for_id_list(base_inventory, facility_id_list,
                                                              inventory_acronyms)
    inventories = getInventoriesforFacilityMatches(inventory_dict, facilitymatches, filter_for_LCI,
                                                   base_inventory)
    # now remove the records from the base_inventory in the facility list
    inventories_index_for_base_but_not_in_facilitylist = inventories[
        (inventories['Source'] == base_inventory) &
        (~inventories['FacilityID'].isin(facility_id_list))].index
    inventories = inventories.drop(inventories_index_for_base_but_not_in_facilitylist, axis=0)
    # Add in chemical matches
    inventories = addChemicalMatches(inventories)

    # Aggregate and remove overlap if requested
    if remove_overlap:
        inventories = aggregate_and_remove_overlap(inventories)

    inventories = addBaseInventoryIDs(inventories, facilitymatches, base_inventory)
    return inventories


def pivotCombinedInventories(combinedinventory_df):
    """Creates a pivot table of combined emissions

    :param combinedinventory_df: pandas dataframe returned from a 'combineInventories..' function
    :return: pandas pivot_table
    """
    # Group the results by facility,flow,and compartment
    # Use a pivot table
    combinedinventory_df_pt = combinedinventory_df.pivot_table(values=['FlowAmount',
                                                                       'DataReliability'],
                                                               index=['FRS_ID',
                                                                      'SRS_ID',
                                                                      'Compartment'],
                                                               columns='Source')
    return combinedinventory_df_pt
