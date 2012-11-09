function callAction(count)
{
	for(var i = count-1; i >= 0; i--)
	{		
		layer = app.activeDocument.artLayers[i];
         var layerName = layer.name;
         layer.visible = true;
         
         var savedState = app.activeDocument.activeHistoryState;
         app.activeDocument.trim(TrimType.TRANSPARENT);
         saveImage(i, layerName);
         app.activeDocument.activeHistoryState = savedState;
         layer.visible = false;
	}
    for(var i = count-1; i >= 0; i--)
	{		
		layer = app.activeDocument.artLayers[i];
		layer.visible = !layer.visible;
	}
}

function saveImage(i, layerName)
{
	var Name = app.activeDocument.name.replace(/\.[^\.]+$/, ''); 
	var Ext = decodeURI(app.activeDocument.name).replace(/^.*\./,''); 
	if(Ext.toLowerCase() != 'psd') return; 
	var Path = app.activeDocument.path; 
	var saveFile = File(Path + "/" + layerName); 
	if(saveFile.exists) saveFile.remove(); 
	if(selectedType=="Save to PNG") SavePNG(saveFile); 
	else SaveJPEG(saveFile); 
}

function SavePNG(saveFile){ 
    pngSaveOptions = new PNGSaveOptions(); 
	activeDocument.saveAs(saveFile, pngSaveOptions, true, Extension.LOWERCASE); 
} 

function SaveJPEG(saveFile){ 
    jpegSaveOptions = new JPEGSaveOptions(); 
	jpegSaveOptions.quality = selectedQuality;
	activeDocument.saveAs(saveFile, jpegSaveOptions, true, Extension.LOWERCASE); 
} 

function Dialog()
{
	saveAsTypeOptions = []; 
	saveAsTypeOptions[0] = "Save to PNG"; 
	saveAsTypeOptions[1] = "Save to JPEG"; 
	
	qualityOptions = [];
	for(var i = 12; i >= 1; i--)
	{
		qualityOptions[i] = i;
	}

	selectedType = ""; 
	var dlg = new Window ('dialog', 'Select type'); 

	dlg.dropdownlist = dlg.add("dropdownlist", undefined,""); 
	dlg.quality = dlg.add("dropdownlist", undefined, "");

	for (var i=0,len=saveAsTypeOptions.length;i<len;i++) 
	{
		dlg.dropdownlist.add ('item', "" + saveAsTypeOptions[i]);      
	}; 
	
	for(var i = 12;i>=1;i--)
	{
		dlg.quality.add ('item', "" + qualityOptions[i]);      
	}; 

	dlg.dropdownlist.onChange = function() { 
	   selectedType = saveAsTypeOptions[parseInt(this.selection)]; 
	   if(selectedType==saveAsTypeOptions[1]) dlg.quality.show();
	   else dlg.quality.hide();
	  }; 
	  	   
	dlg.quality.onChange = function() {
		selectedQuality = qualityOptions[12-parseInt(this.selection)];
	};

	var uiButtonRun = "Continue"; 

	dlg.btnRun = dlg.add("button", undefined ,uiButtonRun ); 
	dlg.btnRun.onClick = function() {	
		this.parent.close(0); }; 
	dlg.orientation = 'column'; 

	dlg.dropdownlist.selection = dlg.dropdownlist.items[0] ;
	dlg.quality.selection = dlg.quality.items[0] ;
	dlg.center(); 
	dlg.show();
}

function hideLayers(count)
{
        for(var i = count-1; i >= 0; i--)
	{		
		layer = app.activeDocument.artLayers[i];
		layer.visible = false;
	}
}

function main()
{
	var count = app.activeDocument.artLayers.length;
	Dialog();
    var firstQ = confirm(selectedType + " may take time depending on the amount of layers. \nThis document contains " + count + " layers. \nImages will be saved in the same directory as this file.\nContinue?");
    if(firstQ) {
		hideLayers(count);
		callAction(count);
		alert("Process complete");
	}	
}

main();