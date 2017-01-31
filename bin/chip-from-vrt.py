# gbdx.Task(chipper-from-vrt, geojson, imagery_location (including bucket name), mosaic=False, aws_access_key=None, aws_secret_key=None, aws_session_token=None, mask=True)
# imagery location can be a path to mosaic project (if mosaic is True) or multiple paths to image strips, each separated by a comma

## ASSUMPTIONS ##

# mosaic data strux will be consistent, as follows:
# # mosaic saved as bucket_name/mosaic_location
# # appropriate vrt shapefile located at .../wms/vsitindex_z12.shp (under other name sspecified in shapefile_name)
# geojson has class_name if chips are for training

import logging
import geojson, json
import subprocess
import os, ast, re

from glob import glob
from functools import partial
from osgeo import gdal
from shutil import copyfile
from multiprocessing import Pool, cpu_count
from gbdx_task_interface import GbdxTaskInterface

# log file for debugging
logging.basicConfig(filename='out.log',level=logging.DEBUG)

def execute_command(cmd):
    '''
    Execute a command. This is outside of the class because you cannot use
        multiprocessing.Pool on a class methods.
    '''
    try:
        subprocess.call(cmd, shell=True)
    except:
        # Don't throw error if chip is ouside raster
        logging.debug('gdal_translate failed for the following command: ' + cmd)
        return True


def mask_chip(feature):
    '''
    Apply polygon mask to bounding-box chips. Chips must be named
        'feature_id.tif' and exist in the current working directory.
    '''
    chip_name = str(feature['properties']['feature_id']) + '.tif'
    fn = str(feature['properties']['feature_id']) + '.geojson'
    chip = gdal.Open(chip_name)

    # Create ogr vector file for gdal_rasterize
    vectordata = {'type': 'FeatureCollection', 'features': [feature]}

    with open(fn, 'wb') as f:
        geojson.dump(vectordata, f)

    # Mask raster
    cmd = 'gdal_rasterize -q -i -b 1 -b 2 -b 3 -burn 0 -burn 0 -burn 0 {} {}'.format(fn, chip_name)
    subprocess.call(cmd, shell=True)

    # Remove ogr vector file
    os.remove(fn)


class ChipFromVrt(GbdxTaskInterface):
    '''
    Extract features in a geojson from imagery
    '''

    def __init__(self):
        '''
        Get inputs
        '''
        GbdxTaskInterface.__init__(self)
        self.execute_command = execute_command
        self.mask_chip = mask_chip

        self.geojson_dir = self.get_input_data_port('geojson')
        self.geojsons = [f for f in os.listdir(self.geojson_dir) if f.endswith('.geojson')]
        self.geojson = os.path.join(self.geojson_dir, self.geojsons[0])
        self.imagery = self.get_input_string_port('images')
        self.mosaic = ast.literal_eval(self.get_input_string_port('mosaic', default='False'))
        self.min_side_dim = int(self.get_input_string_port('min_side_dim', default='0'))
        self.max_side_dim = ast.literal_eval(self.get_input_string_port('max_side_dim', default='None'))
        self.mask = ast.literal_eval(self.get_input_string_port('mask', default='False'))
        self.a_key = self.get_input_string_port('aws_access_key', default=None)
        self.s_key = self.get_input_string_port('aws_secret_key', default=None)
        self.token = self.get_input_string_port('aws_session_token', default=None)
        self.bit_depth = ast.literal_eval(self.get_input_string_port('bit_depth', default='None'))
        self.shapefile = self.get_input_string_port('shapefile_location', default = 'wms/vsitindex_z12.shp')

        # Assert exactly one geojson file passed
        if len(self.geojsons) != 1:
            logging.debug('There are {} geojson files found in the geojson directory'.format(str(len(self.geojsons))))
            raise AssertionError('Please make sure there is exactly one geojson file in the geojson directory. {} found.'.format(str(len(self.geojsons))))

        # Set AWS environment variables
        if self.a_key and self.s_key:
            os.environ['AWS_ACCESS_KEY_ID'] = self.a_key
            os.environ['AWS_SECRET_ACCESS_KEY'] = self.s_key
            logging.info('Set AWS env variables.')

        if self.token:
            os.environ['AWS_SESSION_TOKEN'] = self.token
            logging.info('Set AWS token.')

        # Create output directory
        self.out_dir = self.get_output_data_port('chips')
        os.makedirs(self.out_dir)

        # Format imagery input (list for non-mosaic, string for mosaic)
        # TODO make this a multiplex input port if possible
        if self.mosaic:
            self.imagery = re.sub(r'^s3://', '', self.imagery).strip('/')
        else:
            self.imagery = [img.strip() for img in self.imagery.split(',')]
            for i in range(len(self.imagery)):
                self.imagery[i] = re.sub(r'^s3://', '', self.imagery[i])


    def create_vrt(self):
        '''
        create a vrt from the input imagery. this will return the name of the output file
            if the vrt was made, otherwise an error. The vrt is called 'mosaic.vrt'
            and exists in the current working directory. It either points to the
            mosaic chips or creates a virtual mosaic from input strips (depending on type
            of imagery input to the task)
        '''

        print 'Creating VRT from imagery...'

        # Create vrt from mosaic or input imagery
        if self.mosaic:
            shp_dir = os.path.join('/vsis3', self.imagery, self.shapefile)
            cmd = 'env GDAL_DISABLE_READDIR_ON_OPEN=YES VSI_CACHE=TRUE gdalbuildvrt mosaic.vrt ' + shp_dir
            subprocess.call(cmd, shell=True)

        else:
            img_locs = '' # String of all images
            for img in self.imagery:
                img_locs += os.path.join('/vsis3', img) + ' '

            cmd = 'env GDAL_DISABLE_READDIR_ON_OPEN=YES VSI_CACHE=TRUE gdalbuildvrt mosaic.vrt ' + img_locs.strip()
            subprocess.call(cmd, shell=True)

        # Check that vrt was created
        logging.info('VRT command: ' + cmd)
        if os.path.isfile('mosaic.vrt'):
            return 'mosaic.vrt'

        else:
            logging.debug('VRT not created, check S3 vars and VRT command')
            raise Exception('VRT could not be created. Make sure AWS credentials are accurate and shapefile is in the project/wms/ location if using a mosaic.')


    def generate_feature_ids(self, feature_collection):

        '''
        Create a unique feature id for each geometry in a feature collection, save the
            new feature collection in a geojson to the output directory
        '''
        print 'Generating feature ids... '
        fid = 0
        for feat in xrange(len(feature_collection)):
            feature_collection[feat]['properties']['feature_id'] = fid
            fid += 1

        # Update input geojson with feature ids
        with open(self.geojson) as f:
            data = geojson.load(f)
            data['features'] = feature_collection

        with open(self.geojson, 'w') as f:
            geojson.dump(data, f)

        return feature_collection


    def get_gdal_translate_cmds(self, vrt_file, feature_collection):
        '''
        Generate commands for extracting each chip
        '''
        gdal_cmds = []

        for feat in feature_collection:
            # get bounding box of input polygon
            geom = feat['geometry']['coordinates'][0]
            f_id = feat['properties']['feature_id']
            xs, ys = [i[0] for i in geom], [i[1] for i in geom]
            ulx, lrx, uly, lry = min(xs), max(xs), max(ys), min(ys)

            # format gdal_translate command
            out_loc = os.path.join(self.out_dir, str(f_id) + '.tif')

            if not self.bit_depth:
                cmd = 'gdal_translate -eco -q -projwin {0} {1} {2} {3} {4} {5}'.format(str(ulx), str(uly), str(lrx), str(lry), vrt_file, out_loc)
                print cmd

            else:
                cmd = 'gdal_translate -eco -q -co NBITS={0} -projwin {1} {2} {3} {4} {5} {6}'.format(str(self.bit_depth), str(ulx), str(uly), str(lrx), str(lry), vrt_file, out_loc)

            gdal_cmds.append(cmd)
            logging.info(cmd)

        return gdal_cmds


    def get_ref_geojson(self, open_geoj):
        '''
        create a geojson with only features in chips output directory.
        '''
        # Get list of feature_ids in output directory
        chips = [f[:-4] for f in os.listdir('.') if f.endswith('.tif')]
        feature_collection = open_geoj['features']
        valid_feats = []

        for feat in feature_collection:
            if str(feat['properties']['feature_id']) in chips:
                valid_feats.append(feat)

        open_geoj['features'] = valid_feats
        output_file = os.path.join(self.out_dir, 'ref.geojson')

        with open(output_file, 'wb') as f:
            geojson.dump(open_geoj, f)


    def repeat_for_failed_chips(self, vrt_file, feature_collection):
        '''
        Check which features were not chipped, repeat gdal_translate. Occasional network
            errors cause chipping to fail, this will retry each missed chip
        '''

        # All ids of chipped features
        feats_in_output = [chip[:-4] for chip in os.listdir(self.out_dir) if chip.endswith('.tif')]
        missed_feats = []

        # Detemine features that were missed
        for feat in feature_collection:
            if str(feat['properties']['feature_id']) not in feats_in_output:
                missed_feats.append(feat)

        print 'Re-chipping {} missed features...'.format(str(len(missed_feats)))

        # Get commands to retry gdal_translate
        cmds = self.get_gdal_translate_cmds(vrt_file, missed_feats)
        p = Pool(cpu_count())
        p.map(self.execute_command, cmds)
        p.close()
        p.join()


    def invoke(self):

        '''
        Execute task
        '''
        ##### Get feature collection
        with open(self.geojson) as f:
            data = geojson.load(f)
            feature_collection = data['features']

        ##### Create VRT as a pointer to imagery on S3
        vrt_file = self.create_vrt()

        ##### Generate feature ids if not provided
        if 'feature_id' not in  feature_collection[0]['properties'].keys():
            feature_collection = self.generate_feature_ids(feature_collection)

        ##### Create commands for extracting chips
        cmds = self.get_gdal_translate_cmds(vrt_file, feature_collection)

        ##### Execute gdal_translate commands for chipping in parallel
        print 'Chipping imagery... '
        p = Pool(cpu_count())
        p.map(self.execute_command, cmds)
        p.close()
        p.join()

        ##### Repeat commands for missed features
        self.repeat_for_failed_chips(vrt_file, feature_collection)

        ##### Mask chips in parallel
        os.chdir(self.out_dir) # !!!! Now in output directory !!!!
        if self.mask:
            p = Pool(cpu_count())
            p.map(self.mask_chip, feature_collection)
            p.close()
            p.join()

        ##### Create output geojson for feature_id reference
        self.get_ref_geojson(data)

        ##### Remove msk files
        for fl in glob('*.msk'):
            os.remove(fl)


if __name__ == '__main__':

    with ChipFromVrt() as task:
        task.invoke()
