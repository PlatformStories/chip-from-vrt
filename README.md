# chip-from-vrt

A GBDX task for generating AOI chips from a group of images in an S3 location using [the GDAL virtual format](http://www.gdal.org/gdal_vrttut.html) (vrt). The images can be individual strips or the tiles of a FLAME mosaic. By creating a vrt that points to the remote locations of the imagery, the task can extract pixel data from any number of images without having to mount the entire image to the worker node, thus reducing overhead and bypassing disc space limitations.
AOIs are provided in a geojson file. Chips are saved to a user defined S3 location along with a reference geojson (ref.geojson), which contains the AOIs that were chipped out. Note that ref.geojson will not contain AOIs from the input geojson that failed to be extracted from the imagery. If there is spatial overlap between images, the chip is retrieved from the last image to be listed on the imagery input.


## Run

There are two ways to run chip-from-vrt; chip from a group of individual strips or from a group of tiles that comprise a FLAME mosaic.

### Chip from strips

<img src='images/chip-s3-strips.png' width=500>

1. In a Python terminal create a GBDX interface:

    ```python
    from gbdxtools import Interface
    from os.path import join
    import uuid

    gbdx = Interface()
    input_location = 's3://gbd-customer-data/58600248-2927-4523-b44b-5fec3d278c09/platform-stories/building-detection-large-scale/'

    ```

2. For imagery on the gbd-customer-data bucket you will need to set S3 credentials (including a session token). It is recommended to request credentials that last for 36 hours to ensure they last for the duration of the task. Do so by sending a post to ```https://geobigdata.io/s3creds/v1/prefix?duration=129600``` and using these values as the aws credentials below.

    ```python
    inf = gbdx.s3.info          # Skip this if using 36 hr credentials
    access_key = inf['S3_access_key']
    secret_key = inf['S3_secret_key']
    session_token = inf['S3_session_token']
    ```

3. Create a task instance and set the required [inputs](#inputs):

    ```python
    # Define imagery input location
    imagery_input = join(input_location, 'strip-imagery')

    # Create task and set inputs
    chip_strips = gbdx.Task('chip-from-vrt')
    chip_strips.inputs.geojson = join(input_location, 'strip-geojson')
    chip_strips.inputs.imagery_location = ', '.join([join(imagery_input, '1040010014BCA700.tif'), join(imagery_input, '1040010014800C00.tif')])
    chip_strips.inputs.aws_access_key = access_key
    chip_strips.inputs.aws_secret_key = secret_key
    chip_strips.inputs.aws_session_token = session_token
    ```

4. Set the domain to raid if chipping more than 10000 AOIs to speed up execution:

    ```python
    chip_strips.domain = 'raid'
    ```

5. Create a workflow from the task and specify where to save the output chips:

    ```python
    # Specify output location with random string
    random_str = str(uuid.uuid4())
    output_location = join('platform-stories/trial-runs', random_str)

    chip_strips_wf = gbdx.Workflow([chip_strips])
    chip_strips_wf.savedata(chip_strips.outputs.chips, join(output_location, 'chips'))
    ```

6. Execute the workflow

    ```python
    chip_strips_wf.execute()
    ```

### Chip from FLAME mosaic

<img src='images/chip-s3-mosaic.png' width=500>

1. In a Python terminal create a GBDX interface:

    ```python
    from gbdxtools import Interface
    from os.path import join
    import uuid

    gbdx = Interface()

    # Specify location of input files
    input_location = 's3://gbd-customer-data/58600248-2927-4523-b44b-5fec3d278c09/platform-stories/chip-from-vrt/'
    ```

2. Create a task instance and set the required [inputs](#inputs):

    ```python
    chip_mosaic = gbdx.Task('chip-from-vrt')
    chip_mosaic.inputs.geojson = join(input_location, 'mosaic-geojson/')
    chip_mosaic.inputs.imagery_location = 'flame-projects/335-dikwa-nigeria'
    chip_mosaic.inputs.mosaic = 'True'
    chip_mosaic.inputs.aws_access_key = 'access_key'     # Required for non-public mosaic (need read access)
    chip_mosaic.inputs.aws_secret_key = 'secret_key'     # Required for non-public mosaic (need read access)
    ```

3. Set the domain to raid if chipping more than 10000 AOIs to speed up execution:

    ```python
    chip_mosaic.domain = 'raid'
    ```

4. Create a workflow from the task and specify where to save the output chips:

    ```python
    # Specify output location with random string
    random_str = str(uuid.uuid4())
    output_location = join('platform-stories/trial-runs', random_str)

    # Create the workflow and save the output to output_location
    chip_mosaic_wf = gbdx.Workflow([chip_mosaic])
    chip_mosaic_wf.savedata(chip_mosaic.outputs.chips, join(output_location, 'mosaic-chips'))
    ```

5. Execute the workflow:

    ```python
    chip_mosaic_wf.execute()
    ```


## Input ports

| **Parameter:**  | Description:                                                     |
|-----------------|------------------------------------------------------------------|
| geojson | Directory: Contains a geojson file with geometries to extract from imagery. **Properties must include class names if the chips are to be used for training**. If a feature_id property is not provided it will be generated by the task and included in ref.geojson. |
|  imagery | String: Location of all images to extract AOI data from. If using image strips this will be in the form of full paths to wach image separated by commas, (bucket/path/to/image_1.tif, bucket/path/to/image_2.tif). This is how the vrt will know which imagery to extract pixels from. If using a FLAME mosaic this should be the path to a project directory (bucket/path/to/project/) |  
|  mosaic | String ('boolean'): True if imagery is a mosaic (task will expect imagery_location to be a FLAME project directory). If False task will expect one or more paths to individual strips. |  
|  aws_access_key | String: AWS access key that has read rights to imagery bucket (only necessary if imagery is in private bucket). |  
|  aws_secret_key | String: AWS secret access key with read rights to imagery bucket (only necessary if imagery is in private bucket). |  
|  aws_session_token | String: AWS session token for imagery bucket. Required for imagery in the gbd-customer-data bucket. It is recommended to use a 36-hour token (gbdxtools currently defaults to 10 hours). |  
|  mask | String ('boolean'): Blackfill pixels outside the polygon. Otherwise entire bounding box will be included in the output chip. |  

## Output Ports

| Name  | Type | Description:                                      |
|-------|---------|---------------------------------------------------|
| chips | Directory | Contains all chipped AOIs saved as feature_id.tif. There is also a reference geojson 'ref.geojson' that lists all chipped AOI geometries, feature ids, and class names (if provided). |

## Advanced

### Internal Tiling for Faster Chipping

When chipping a large number of AOIs (>10000) from image strips it is recommended to use internal tiling to speed up the task. To accomplish this you may use the ```tile-strips``` gbdx task on each image as follows:

```python
from gbdxtools import Interface()
gbdx = Interface()

tiler = gbdx.Task('tile-strips', images = 's3://bucket/prefix/path/to/imagery/')
tiler_wf = gbdx.Task([tiler])

# This will overwrite original images with tiled versions
tiler_wf.savedata(tiler.outputs.tiled_images, 'path/to/imagery/')

tiler_wf.execute()
```

### Virtual Datasets and Image Overlap

The task uses [gdalbuildvrt](http://www.gdal.org/gdalbuildvrt.html) to create a virtual dataset that combines all input imagery and points to the various locations of each strip on S3. The task uses this vrt as a reference for where to find the pixel data of each AOI. If there is spatial overlap between images, data is extracted from the latest image listed. The order that the images are input to the task is maintained when calling gdalbuildvrt. 


## Development

### Build the Docker image

You need to install [Docker](https://docs.docker.com/engine/installation/).

Clone the repository:

```bash
git clone https://github.com/platformstories/chip-from-vrt
```

Then:

```bash
cd chip-from-vrt
docker build -t yourusername/chip-from-vrt .
```

Then push the image to Docker Hub:

```bash
docker push yourusername/chip-from-vrt
```

The image name should be the same as the image name under containerDescriptors in chip-from-vrt.json.


### Register on GBDX

In a Python terminal:

```python
import gbdxtools
gbdx = gbdxtools.Interface()
gbdx.task_registry.register(json_filename='chip-from-vrt.json')
```
