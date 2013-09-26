% Same as h5info, but only the basics (and doesn't crash)
% This is only as a temporary fix because versions of Matlab before R2013a 
% crash when reading the HDF5 files from SVI and Odemis. This is due to error
% in handling enumerated types, and anyway, we don't need to know about them.

% Based on h5load.m by Pauli Virtanen <pav@iki.fi> (Public Domain)

% Only report the names of groups and datasets, and the shape of datasets.
function [data] = h5basicinfo(filename)
	loc = H5F.open(filename, 'H5F_ACC_RDONLY', 'H5P_DEFAULT');
	try
	  data = load_group(loc, '');
	  H5F.close(loc);
	catch exc
	  H5F.close(loc);
	  rethrow(exc);
    end
    data.Filename = filename;
end

function data = load_group(loc, name)
	% Load a record recursively.
	% data is a struct which contains:
	% .Name: string defining the full name
	% .Groups : array of subgroups
	% .Datasets: arrays of datasets
	
	data = struct();
	if strcmp(name, '') % special name for root
		data.Name = '/';
	else
		data.Name = name;
	end
	data.Groups = [];
	data.Datasets = [];

	% Load groups and datasets
	num_objs = H5G.get_num_objs(loc);
	for j_item=0:num_objs-1,
	  objtype = H5G.get_objtype_by_idx(loc, j_item);
	  objname = H5G.get_objname_by_idx(loc, j_item);
	  
	  if objtype == 0
		% Group
		group_loc = H5G.open(loc, objname);
		try
		  sub_data = load_group(group_loc, [name '/' objname]);
		  H5G.close(group_loc);
		catch exc
		  H5G.close(group_loc);
		  rethrow(exc);
		end
		data.Groups = [data.Groups; sub_data];
	   
	  elseif objtype == 1
		% Dataset
		dataset_loc = H5D.open(loc, objname);
		try
			sub_data = load_dataset(dataset_loc, objname);
			H5D.close(dataset_loc);
		catch exc
			H5D.close(dataset_loc);
			rethrow(exc);
		end
		
		data.Datasets = [data.Datasets; sub_data];	
	  end
	  
	end
end

function data = load_dataset(loc, name)
	% .Name: string of the base name (without the path)
	% .Dataspace: struct of .Size and .MaxSize: array of the dimensions and maximum dimensions
	data = struct();
	data.Name = name;
    space_id = H5D.get_space(loc);
    [ndims, h5_dims h5_mdims] = H5S.get_simple_extent_dims(space_id);
	data.Dataspace = struct();
	data.Dataspace.Size = fliplr(h5_dims);
	data.Dataspace.MaxSize = fliplr(h5_mdims);
end
