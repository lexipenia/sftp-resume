# sftp-resume

A simple terminal-based SFTP client written in Python with paramiko. If the SFTP connection is interrupted, the client will resume and finish all pending downloads.

At present, there is no upload functionality.

## Dependencies

```
$ pip install paramiko alive_progress
```

## Usage

Fill in configuration details in the `config.py` file.

`remoteDir` can be used to navigate automatically to a specific folder on your server where downloads are located.

The target directory for downloads is normally set at the beginning by dragging/dropping. You can modify the program to skip this step and specify `targetDir` in the config file if you wish.

Simply run the program from the command line:

```
$ python sftp-resume.py
```
