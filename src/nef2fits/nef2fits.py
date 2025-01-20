import sys 
import os
import re
import json
import argparse
import errno
from datetime import datetime

import rawpy
import piexif
import watchdog
from watchdog import events,observers

from astropy.io import fits
import astropy


# constants

default_object_regex = r"(?:.+-)?([^_\s]+)(?:_.+)?"
__version__ = "1.1"

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

def timestamp():
    return f"[{datetime.now()}]"

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

    Returns the name of the new FITS file.
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
    return output_fname
    #print("converted",path,"to fits format, exported to:",output_fname)


# hate this way of doing things, but this is the only way to invoke code from the watchdog
class NEF2FITSEventHandler(watchdog.events.FileSystemEventHandler):
    """Event handler to use with a watchdog observer. The init keyword arguments are all passed directly to the nef2fits function."""
    def __init__(self, **kwargs):
        super().__init__()
        self.options = kwargs
        
    def on_moved(self, event):
        super().on_moved(event)
        if not event.is_directory:
            src = event.src_path
            root,ext = os.path.splitext(src)
            if ext == ".nef":
                dest = event.dest_path
                root_dest,_ = os.path.splitext(dest)
                print(timestamp(),end=" ")
                print(f"File '{os.path.basename(src)}' was moved into '{os.path.basename(dest)}'. Moving the FITS file aswell...")
                old_fits = root+'.fits'
                if os.path.exists(old_fits):
                    os.remove(old_fits)
                nef2fits(dest,**self.options)

    def on_created(self, event):
        super().on_created(event)
        if not event.is_directory:
            path = event.src_path
            root,ext = os.path.splitext(path)
            if ext == ".nef":
                print(timestamp(),end=" ")
                print(f"Created NEF file '{os.path.basename(root)}', converting into FITS...")
                nef2fits(path,**self.options)
                #print(f"\t\tDEBUG: creating {root+'.fits'}")

        #print(event)
        
    def on_deleted(self, event):
        super().on_deleted(event)
        if not event.is_directory:
            src = event.src_path
            root,ext = os.path.splitext(src)
            if ext == ".nef":
                print(timestamp(),end=" ")
                print(f"File '{os.path.basename(src)}' was deleted, but I'm not deleting any corresponding FITS files.")
        
    def on_modified(self, event):
        super().on_modified(event)
        #print(event)
    




def watch(directory:str=".",recursive=False,timeout=0.1,**kwargs):
    event_handler = NEF2FITSEventHandler(**kwargs)
    observer = watchdog.observers.Observer(timeout=timeout)
    observer.schedule(event_handler,directory,recursive=recursive)
    try:
        observer.start()
        print(timestamp())
        print("nef2fits started watching",directory,recursive*"(recursively)","for changes.")
        print("When a .nef file is created or modified, it will be automatically converted to FITS.")
        print("*"*80)
        while True:
            observer.join(timeout)
    except KeyboardInterrupt:
        print("\n"+"*"*80)
        print("nef2fits was interrupted manually, stopping the watch! Bye bye.")
        observer.stop()
    finally:
        observer.join()
        observer.stop()
    

def main():
    # arg parser definition
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(title='Subcommands',description='Valid subcommands',dest="command")
    # subparsers
    convert_parser = subparsers.add_parser("convert",help="Input .nef files to convert")
    watch_parser = subparsers.add_parser("watch",help="Watch directory for new .nef files and convert them automatically")
    # common arguments
    convert_parser.add_argument("files",type=str,nargs="+",help=".nef files to process. Multiple files are converted sequentially.")
    watch_parser.add_argument("directory",type=str,default=".",help="Directory to watch for new .nef files to process. FITS files are not overwritten.")
    watch_parser.add_argument("-r","--recursive",action="store_true",help="Whether the watching is done recursively or not.")

    for p in [convert_parser,watch_parser]:
        p.add_argument("-p","--prefix",default="",help="Folder to output the converted files. "
                        "If `--prefix='baz'`, `./foo/00.nef` would be converted to `./baz/foo/00.fits`.")
        p.add_argument("--header",type=str,help="JSON File with extra elements to be appended to the FITS header. "
                        "Must be an array, and each element must be a (key,value) array or (key,value,comment) array.")
        p.add_argument("-o","--overwrite",action="store_true",default=True,help="whether to overwrite files (the default) or not.")

    # argument handling
    args = parser.parse_args()
    header = []

    if args.header is not None and os.path.exists(args.header):
        with open(args.header) as file:
            header = [*map(tuple,json.load(file))]
    
    # processing 
    match args.command:
        case "convert":
            for path in args.files:
                if os.path.exists(path):
                    output_fname = nef2fits(path,header_constants=header,prefix=args.prefix,overwrite=args.overwrite)
                    print("converted",path,"to fits format, exported to:",output_fname)
                else:
                    raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT),path)
        case "watch":
            try:
                watch(args.directory,recursive=args.recursive,header_constants=header,prefix=args.prefix,overwrite=args.overwrite)
            except Exception as e:
                print(timestamp(),end=" ")
                print(f"Exception happened: {e}, but I will not stop watching.")
            

    #if ""
    #for path in 

    #parser = argparse.ArgumentParser(prog="nef2fits",description="Image converter from NEF to FITS format")
    #subparsers = parser.add_subparsers(title="Subcommands",help="")
    #watch_parser = subparsers.add_parser("watch",help="Watch directory for new .nef files and convert automatically.")
#
    #if "watch" not in sys.argv:
    #    parser.add_argument("files",type=str,nargs="+",help=".nef files to process. Multiple files are converted sequentially.")
    #watch_parser.add_argument("directory",type=str,help="Directory to watch for new .nef files to process. FITS files are not overwritten.")
    #watch_parser.add_argument("-r","--recursive",action="store_true",help="Whether the watching is done recursively or not.")
#
    #for p in [parser,watch_parser]:
    #    p.add_argument("-p","--prefix",default="",help="Folder to output the converted files. "
    #                   "If `--prefix='baz'`, `./foo/00.nef` would be converted to `./baz/foo/00.fits`.")
    #    p.add_argument("--header",type=str,help="JSON File with extra elements to be appended to the FITS header. "
    #                   "Must be an array, and each element must be a (key,value) array or (key,value,comment) array.")
    
    
    #watch_parser.add_argument("-p","--prefix",default="",help="Folder to output the converted files. "
    #                          "If `--prefix='baz'`, `./foo/00.nef` would be converted to `./baz/foo/00.fits`.")
    #watch_parser.add_argument("--header",type=str,help="JSON File with extra elements to be appended to the FITS header. "
    #                       "Must be an array, and each element must be a (key,value) array or (key,value,comment) array.")
    #watch_parser.add_argument("-r","--recursive",action="store_true",help="Whether the watching is done recursively or not.")
    #
    #parser.add_argument("-p","--prefix",default="",help="Folder to output the converted files. "
    #                    "If `--prefix='baz'`, `./foo/00.nef` would be converted to `./baz/foo/00.fits`.")
    ##parser.add_argument("-o","--overwrite",action="store_true",help="whether to overwrite files (the default) or not.")
    #parser.add_argument("--header",type=str,help="JSON File with extra elements to be appended to the FITS header. "
    #                    "Must be an array, and each element must be a (key,value) array or (key,value,comment) array.")
    
    
    #args = parser.parse_args()
    #print(args)
    #if args.header is None:
    #    header = []
    #elif os.path.exists(args.header):
    #    with open(args.header) as file:
    #        header = [*map(tuple,json.load(file))]
   # 
    #for path in args.files:

if __name__ == "__main__":
    main()