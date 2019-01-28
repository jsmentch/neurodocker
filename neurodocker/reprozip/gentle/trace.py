import logging
import os

from neurodocker.utils import get_docker_client

BASE_PATH = os.path.dirname(os.path.realpath(__file__))
TRACE_SCRIPT = os.path.join(BASE_PATH, '_trace.sh')
PRUNE_SCRIPT = os.path.join(BASE_PATH, '_prune.py')
logger = logging.getLogger(__name__)


def copy_file_to_container(container, src, dest):
    """Copy `local_filepath` into `container`:`container_path`.

    Parameters
    ----------
    container : str or container object
        Container to which file is copied.
    src : str
        Filepath on the host.
    dest : str
        Directory inside container. Original filename is preserved.

    Returns
    -------
    success : bool
        True if copy was a success. False otherwise.
    """
    # https://gist.github.com/zbyte64/6800eae10ce082bb78f0b7a2cca5cbc2

    from io import BytesIO
    import tarfile

    client = get_docker_client()

    try:
        container.put_archive
        container = container
    except AttributeError:
        container = client.containers.get(container)

    with BytesIO() as tar_stream:
        with tarfile.TarFile(fileobj=tar_stream, mode='w') as tar:
            filename = os.path.split(src)[-1]
            tar.add(src, arcname=filename, recursive=False)
        tar_stream.seek(0)
        return container.put_archive(dest, tar_stream)


def trace_and_prune(container, commands, directories_to_prune):
    client = get_docker_client()
    container = client.containers.get(container)

    if isinstance(commands, str):
        commands = [commands]

    if isinstance(directories_to_prune, str):
        directories_to_prune = [directories_to_prune]

    cmds = ' '.join('"{}"'.format(c) for c in commands)

    copy_file_to_container(container, TRACE_SCRIPT, '/tmp/')
    trace_cmd = "bash /tmp/_trace.sh " + cmds
    logger.info("running command within container {}: {}"
                 "".format(container.id, trace_cmd))

    _, log_gen = container.exec_run(trace_cmd, stream=True)
    for log in log_gen:
        log = log.decode().strip()
        logger.info(log)
        if (("REPROZIP" in log and "couldn't use ptrace" in log)
                or "neurodocker (in container): error" in log.lower()
                or "_pytracer.Error" in log):
            raise RuntimeError("Error: {}".format(log))

    # Get files to prune.
    copy_file_to_container(container, PRUNE_SCRIPT, '/tmp/')
    ret, result = container.exec_run(
        "/tmp/reprozip-miniconda/bin/python /tmp/_prune.py"
        " --config-file /tmp/neurodocker-reprozip-trace/config.yml"
        " --dirs-to-prune {}".format(" ".join(directories_to_prune)).split())
    result = result.decode().strip()
    if ret:
        raise RuntimeError("Failed: {}".format(result))

    ret, result = container.exec_run(
        ['cat', '/tmp/neurodocker-reprozip-trace/TO_DELETE.list'])
    result = result.decode().strip()
    if ret:
        raise RuntimeError("Error: {}".format(result))

    files_to_remove = result.splitlines()
    if not len(files_to_remove):
        print("No files to remove. Quitting.")
        return

    print("WARNING!!! THE FOLLOWING FILES WILL BE REMOVED:")
    print('\n    ', end='')
    print('\n    '.join(sorted(files_to_remove)))

    print(
        '\nWARNING: PROCEEDING MAY RESULT IN IRREVERSIBLE DATA LOSS, FOR EXAMPLE'
        ' IF ATTEMPTING TO REMOVE FILES IN DIRECTORIES MOUNTED FROM THE HOST.'
        ' PROCEED WITH EXTREME CAUTION! NEURODOCKER ASSUMES NO RESPONSIBILITY'
        ' FOR DATA LOSS. ALL OF THE FILES LISTED ABOVE WILL BE REMOVED.')
    response = 'placeholder'
    try:
        while response.lower() not in {'y', 'n', ''}:
            response = input('Proceed (y/N)? ')
    except KeyboardInterrupt:
        print("\nQuitting.")
        return

    if response.lower() in {'', 'n'}:
        print("Quitting.")
        return

    print("Removing files ...")
    ret, result = container.exec_run(
        'xargs -d "\n" -a /tmp/neurodocker-reprozip-trace/TO_DELETE.list rm -f')
    result = result.decode().split()
    if ret:
        raise RuntimeError("Error: {}".format(result))

    print("Finished removing files.")
    print("Next, create a new Docker image with the minified container:")
    print("\n    docker export {} | docker import - imagename\n".format(container.name))


def main():
    from argparse import ArgumentParser
    p = ArgumentParser(description=__doc__)
    p.add_argument("-c", "--container", required=True, help="Running container.")
    p.add_argument("-d", "--dirs-to-prune", required=True, nargs='+', help="Directories to prune. Data will be lost in these directories.")
    p.add_argument("--commands", nargs='+', help="Commands to minify.")
    args = p.parse_args()

    trace_and_prune(container=args.container, commands=args.commands, directories_to_prune=args.dirs_to_prune)
