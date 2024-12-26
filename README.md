# nef2fits

Python package to convert from Nikon Electronic Format (RAW photos) to the astronomical Flexible Image Transport System.

The usage inside of python is:
```python
from nef2fits import nef2fits
nef2fits("./path/to/file.nef")
```
And a `"./path/to/file.fits"` file would be created. The FITS file will have 4 images, one for each filter of the Bayer matrix of the NEF image (Red, Green 1, Green 2, Blue). Observation date, camera model, saturation value, exposition time and other EXIF tags are transferred.

Custom header values can be provided as a list of (key,value) or (key,value,comment) items.

The package provides a shell script:
```bash
nef2fits file1.nef file2.nef file3.nef --prefix "converted" --header header.json
```

Where the file `head.json` could look like
```json
[
    ["TELESCOP", "Meade LX200", "Model of the telescope used"],
    ["OBSERVER", "Your name here"],
    ["OBJECT", "M42"]
]
```
and those will be transferred to the three newly created FITS files, inside a new folder name "converted", specified by the `--prefix` option.



### Dependencies

* `astropy` for the FITS handling
* `rawpy` to extract the numeric data of the image
* `piexif` to extract the image metadata

### Installation

For now, `pip install .` on this repo. 


## Roadmap and ideas

- [ ] Publish to PyPI.
- [ ] Warning if the max pixel value is near the saturation level.
- [ ] Callable argument to extract header elements from the data itself, not only the NEF exif. For example, max, min, sky values...
- [ ] Option to automatically use a local `header.json` file if found on the same folder on the image.
- [ ] Option to separate the R, G1, G2 and B channels into different fit files rather than one file with 4 arrays.
- [ ] Shell option to modify the object regex.
