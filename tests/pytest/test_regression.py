import os
from os.path import join
import yaml
import shutil
import subprocess
import pytest
import fv3config
from glob import glob


TEST_DIR = os.path.dirname(os.path.realpath(__file__))
REFERENCE_DIR = os.path.join(TEST_DIR, 'reference')
OUTPUT_DIR = os.path.join(TEST_DIR, 'output')
CONFIG_DIR = os.path.join(TEST_DIR, 'config')
SUBMIT_JOB_FILENAME = os.path.join(TEST_DIR, 'run_files/submit_job.sh')
STDOUT_FILENAME = 'stdout.log'
STDERR_FILENAME = 'stderr.log'
MODEL_IMAGE = 'us.gcr.io/vcm-ml/fv3gfs-compiled-default'
MD5SUM_FILENAME = "md5.txt"

USE_LOCAL_ARCHIVE = True

config_filenames = os.listdir(CONFIG_DIR)


@pytest.fixture
def model_image():
    return MODEL_IMAGE


@pytest.fixture
def reference_dir(request):
    return request.config.getoption("--refdir")


@pytest.fixture(params=config_filenames)
def config(request):
    config_filename = os.path.join(CONFIG_DIR, request.param)
    with open(config_filename, 'r') as config_file:
        return yaml.safe_load(config_file)


def test_regression(config, model_image, reference_dir):
    run_name = config['experiment_name']
    run_reference_dir = os.path.join(reference_dir, run_name)
    run_dir = os.path.join(OUTPUT_DIR, run_name)
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir)
    write_run_directory(config, run_dir)
    run_model(run_dir, MODEL_IMAGE)
    md5sum_filename = os.path.join(run_reference_dir, MD5SUM_FILENAME)
    if not os.path.isfile(md5sum_filename):
        assert False, (f"reference md5sum does not exist, "
                       f"you can create it with `bash set_reference.sh {reference_dir}`"
                       " after running the model and generating the output directories."
                       )
    else:
        check_md5sum(run_dir, md5sum_filename)
    shutil.rmtree(run_dir)


def run_model(rundir, model_image):
    if USE_LOCAL_ARCHIVE:
        archive = fv3config.get_cache_dir()
        archive_mount = ['-v', f'{archive}:{archive}']
    else:
        archive_mount = []
    docker_run = ['docker', 'run', '--rm']
    rundir_abs = os.path.abspath(rundir)
    rundir_mount = ['-v', f'{rundir_abs}:/rundir']
    fv3out_filename = join(rundir, 'stdout.log')
    fv3err_filename = join(rundir, 'stderr.log')
    with open(fv3out_filename, 'w') as fv3out_f, open(fv3err_filename, 'w') as fv3err_f:
        subprocess.check_call(
            docker_run + rundir_mount + archive_mount + [model_image] + ["bash", "/rundir/submit_job.sh"],
            stdout=fv3out_f,
            stderr=fv3err_f
        )


def check_md5sum(run_dir, md5sum_filename):
    subprocess.check_call(["md5sum", "-c", md5sum_filename], cwd=run_dir)


def write_run_directory(config, dirname):
    fv3config.write_run_directory(config, dirname)
    shutil.copy(SUBMIT_JOB_FILENAME, os.path.join(dirname, 'submit_job.sh'))


if __name__ == '__main__':
    pytest.main()