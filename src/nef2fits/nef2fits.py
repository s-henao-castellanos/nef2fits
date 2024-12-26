import sys 
import os
import re
import json
import argparse
from datetime import datetime

import rawpy
import piexif

from astropy.io import fits
import astropy

# constants

default_object_regex = r"(?:.+-)?([^_\s]+)(?:_.+)?"
__version__ = "1.0"

# helpers to decode exif dictionaries 
def fraction(tup):
    if len(tup) == 2:
        num,den = tup
        return num/den
    else:
        return float("nan")
def integer(i):
    match i:
        case int():
            return i
        case bytes():
            return int(i)
        case tuple():
            return tuple(map(int,i))
def decode(s):
    return s.decode()

exif_types = {
    1: integer, #Byte
    2: decode, #Ascii
    3: integer, #Short
    4: integer, #Long
    5: fraction, #Rational
    6: integer, #SByte
    7: bytes, #Undefined
    8: integer, #SShort
    9: integer, #SLong
    10: fraction, #SRational
    11: float, #Float
    12: float, #DFloat
}

exif_tags = piexif.TAGS["Exif"]

def exif_info(path:str) -> dict:
    """Extracts EXIF metadata from image file on `path`. Translates TIFF keyword codes, and deletes MakerNote and XMLPacket."""
    exif_data = piexif.load(path)
    translated = {}
    for subdict in ["Exif","0th"]:
        tags = piexif.TAGS[subdict]
        for k,v in exif_data[subdict].items():
            C = exif_types[tags[k]["type"]]
            value = C(v)
            translated[ tags[k]["name"] ] = value
    for k in ["MakerNote","XMLPacket"]:
        translated.pop(k,None)
    return translated


# default exif dict to header callable

def Nikon_header_from_exif(exif_dict:dict) -> list[tuple]:
    """Expects the exif_dict, already translated from exif_translated_dict()"""
    
    date,time = exif_dict["DateTimeOriginal"].split() # this needs attention
    
    to_add = [
        # keyword    value                          comment
        ("EXPOSURE", exif_dict[f"ExposureTime"],    "Exposure time in seconds"             ),
        ("ISOSPEED", exif_dict[f"ISOSpeedRatings"], "Camera ISO speed sensitivity rating"  ),
        ("CAMERA",   exif_dict[f"Model"],           "Camera model"                         ),
        ("DATE-OBS", date.replace(":","/"),         "YYYY/MM/DD"                           ),
        ("TIME-OBS", time,                          "hh:mm:ss"                             ),
        ("DETECTOR", f"Full-frame DSLR CMOS"                                               ),
        ("PIXSIZE1", 4.88,                          "Micrometers"                          ),
        ("PIXSIZE2", 4.88,                          "Micrometers"                          ),
        ("SATURATE", 2**14,                         "14-bit saturation value"              ),
        ("SWCREATE", exif_dict[f"Software"],                                               ),
    ]
    return to_add


def versions_comment() -> str:
    versions = {k:"?" for k in ["Python","Astropy","rawpy","libraw","piexif"]}
    try:
        versions["Python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    except AttributeError:
        pass
    try:
        versions["Astropy"] = astropy.version.version
    except AttributeError:
        pass
    try:
        versions["rawpy"] = rawpy._version.__version__
    except AttributeError:
        pass
    try:
        versions["libraw"] = ".".join(map(str,rawpy.libraw_version))
    except AttributeError:
        pass
    try:
        versions["piexif"] = piexif.VERSION
    except AttributeError:
        pass
    return "made with: "+", ".join([f"{k} v{v}" for k,v in versions.items()])
    

def nef2fits(
        path:str,
        header_from_exif_callable=Nikon_header_from_exif,
        header_constants=[],
        overwrite=True,
        prefix="",
        object_regex=default_object_regex
        ):
    """Converts a NEF file into FITS.
    
    * path: path to the file to convert. Last component, without extension, will be stored as FILENAME on the header.
    
    * header_from_exif_callable: function that outputs a list of (key,value) or (key,value,comment) items to append to the FITS header. 
        This will depend on the camera, the default is for a Nikon D810A.
        
    * header_constants: list of constant (key,value) or (key,value,comment) items to be appendend.
        If any of these has the same key as one already present on the header, it will overwrite it.
    
    * overwrite: if False, stops if output file exists. Default is True.
    
    * prefix: string to be added atthe start of path. If prefix is 'baz', `./foo/00.nef` would be converted to `./baz/foo/00.fits`.
    
    * object_regex: Applied to the filename (last component of the `path` string) to determine the OBJECT and IMAGETYP. Only group 1 is considered.
        If regex fails. the whole filename without extension is saved as OBJECT.
        The default regex assumes the filename is something like "observationcode-objectname_details_about_image.nef"
        Only objectame would be extracted for the keywords.
    """
    # filename considerations
    root,ext = os.path.splitext(path)
    output_fname = os.path.join(prefix,root+".fits")
    basename = os.path.basename(root)
    try:
        object_name = re.search(object_regex,basename).group(1)
    except AttributeError:
        print("Warning: regex failed with filename",basename,"from",path)
        object_name = basename
    object_name_std = object_name.upper().strip()
    imagetyp = "OBJECT"
    for s in ["BIAS","FLAT","DARK"]:
        if s in object_name_std:
            imagetyp = s
            break
    
    
    # import data
    img = rawpy.imread(path).raw_image
    data = {
        "R": img[0::2,0::2],
        "G1":img[0::2,1::2],
        "B": img[1::2,0::2],
        "G2":img[1::2,1::2],
    }
    # build header from EXIF
    exif_dict = exif_info(path)
    now = str(datetime.now()) 
    header_common = [("EXTEND",True)] + \
        header_from_exif_callable(exif_dict) + \
        [
            ("ORIGIN","nef2fits","FITS file originator"),
            ("SWMODIFY", f"nef2fits v{__version__}"),
            ("FILENAME",root,"orignal filename"),
            ("IMAGETYP",imagetyp,"image calibration class or OBJECT"),
            ("OBJECT",object_name,"Target object name"),
        ] 
    # build HDUs
    hdus = []
    for i,(filter,image) in enumerate(data.items()):
        C = fits.PrimaryHDU if i==0 else fits.ImageHDU
        hdus.append(C(image))
        hdus[-1].name = filter
        hdus[-1].header["EXTEND"] = True
        hdus[-1].header["FILTER"] = f"Photographic {filter[0]}"
        hdus[-1].header.extend(header_common,strip=False,update=True) # first the common ones
        hdus[-1].header.extend(header_constants,strip=False,update=True) # then the user provided constants, that overwrite those
        hdus[-1].header.add_comment(versions_comment())
        hdus[-1].header.add_history(f"Converted from NEF to FITS on {now} UTC-5")
    hdul = fits.HDUList(hdus=hdus)
    # export
    if prefix:
        os.makedirs(os.path.dirname(output_fname),exist_ok=True)
    hdul.writeto(output_fname,overwrite=overwrite)
    print("converted",path,"to fits format, exported to:",output_fname)


def main():
    parser = argparse.ArgumentParser(prog="nef2fits",description="Image converter from NEF to FITS format")
    
    parser.add_argument("files",type=str,nargs="+",help=".nef files to process. Multiple files are converted sequentially.")
    parser.add_argument("-p","--prefix",default="",help="Folder to output the converted files. "
                        "If `--prefix='baz'`, `./foo/00.nef` would be converted to `./baz/foo/00.fits`.")
    #parser.add_argument("-o","--overwrite",action="store_true",help="whether to overwrite files (the default) or not.")
    parser.add_argument("--header",type=str,help="JSON File with extra elements to be appenden to the FITS header. "
                        "Must be an array, and each element must be a (key,value) array or (key,value,comment) array.")
    
    
    args = parser.parse_args()
    if args.header is None:
        header = []
    elif os.path.exists(args.header):
        with open(args.header) as file:
            header = [*map(tuple,json.load(file))]
    
    for path in args.files:
        nef2fits(path,header_constants=header,prefix=args.prefix)


if __name__ == "__main__":
    main()