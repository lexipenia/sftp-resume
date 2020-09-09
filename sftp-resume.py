import os
from time import sleep, time
from datetime import timedelta
from collections import deque
from statistics import mean

from paramiko import Transport, SFTPClient
from alive_progress import alive_bar

from config import *

# format for storing info on each individual file, including its full path
class File:
  def __init__(self,a,b,c):
    self.name = a
    self.path = b
    self.size = c

# main function: connect and then select the files to download
# actual downloads create a new SFTP connection in order to resume on break
def main():

    # remove these two lines if specifying targetDir from config
    global targetDir 
    targetDir = input("Drag and drop to select target local directory:\n").replace("\\", "").strip()

    global remoteDir # allow us to change this when navigating

    print("Connecting to the server…")

    with createSFTPClient(myHostname, myPort, myUsername, myPassword) as sftp:

        print("Connection established successfully.")
        sftp.chdir(remoteDir)

        # select the base directory we want to download from, then select files; repeat loop on error
        while(True):

            directory_structure = sftp.listdir_attr()
            directory_structure.sort(key = lambda x: x.st_mtime, reverse=True)  # sort by date, newest first
            for x in reversed(directory_structure):                             
                print(directory_structure.index(x),"|",getSize(x),"|", x.filename)

            print("\nCurrent directory:",remoteDir)

            nav_choice = input("\nType \"cd\" and a directory number (or ..) to change directory or enter files to download separated by comma, e.g. \"cd 12\", \"cd ..\" or \"1,3,5\".\n")

            if nav_choice[:3] == "cd ":
                try:
                    if nav_choice == "cd ..":
                        sftp.chdir("..")
                        remoteDir = sftp.getcwd()   # we must reset this so the "download" function picks it up
                    else:
                        index = int(nav_choice[3:])
                        sftp.chdir(directory_structure[index].filename)
                        remoteDir = sftp.getcwd()    
                except Exception as e:
                    print("Input error:\n", str(e))
                    print("Let’s try again in 5 seconds…")
                    sleep(5)
                    continue
            else:
                try:
                    # collect names of the root files/folders to download
                    root_folders = [] 
                    indices = nav_choice.split(",")
                    for index in indices:
                        root_folders.append(directory_structure[int(index)].filename)

                    # get all the required info on each file for the download function
                    print("Adding files to download queue…")
                    all_files = []
                    for item in root_folders:
                        getFileInfo(item,"",all_files,sftp)  # pass on empty base path + sftp client
                    break
                except Exception as e:
                    print("Input error:\n", str(e))
                    print("Let’s try again in 5 seconds…")
                    sleep(5)
                    continue

        # if connection breaks keep trying until we have all the files!
        while(True):
            try:
                download(all_files)
                print("All downloads finished.")
                exit()
            except Exception as e:
                if str(e).strip() == "Server connection dropped:":
                    print("Connection error occurred. Retrying in 10 seconds…")
                    sleep(10)
                else:
                    print("An error occurred:\n" + str(e))
                    print("Quitting…")
                    exit()

# create a paramiko SFTP client
def createSFTPClient(host, port, username, password):
    transport = Transport((host,port))
    transport.connect(None,username,password)
    return SFTPClient.from_transport(transport)

# get size of files for the main list, try to make columns uniform width
def getSize(dir):
    if dir.st_mode == 33204: # file
        size_string = tidySize(dir.st_size)
        if len(size_string) < 9:
            diff = 9 - len(size_string)
            return size_string + " "*diff
        else:
            return size_string
    else:
        return "---------"

# tidy size format + output string with unit
def tidySize(size):
    if size > 1000000000:
        return "%.2f" % (size/1000000000) + " GB"
    elif size > 1000000:
        return "%.2f" % (size/1000000) + " MB"
    elif size > 1000:
        return "%.2f" % (size/1000) + " kb"
    else:
        return str(size) + " b"

# recursively build list of files to download 
def getFileInfo(filename, base_path, target_list, sftp_client):

    file_path = base_path + filename                    
    file_type = sftp_client.lstat(file_path).st_mode    # 16877 = dir with permissions; 16893 = dir w/out permissions; 33204 = file 

    if file_type == 33204:                       
        addFileToList(filename,file_path,target_list,sftp_client)
    elif file_type == 16893 or file_type == 16877:
        new_dir = sftp_client.listdir(file_path)
        for x in new_dir:
            getFileInfo(x,file_path + "/",target_list,sftp_client)  # turn filepath+name into new base bath

# create a new "File" object and add it to the downloads list
def addFileToList(filename,file_path,target_list,sftp_client):

    new_file = File(
        filename,
        file_path,
        sftp_client.stat(file_path).st_size
    )

    target_list.append(new_file)

# download all the files in the list, creating a new SFTP client each time the function runs
def download(file_list):

    # track progress: make these properties global to manipulate with the callback function
    global progress
    progress = {
        "total_size": sum(x.size for x in file_list),
        "last_file_total": 0,
        "total_down": 0,
        "last_time": 0,
        "speed_series": deque([],maxlen=40),     # store the last 40 speeds to give 20 sec smoother average
    }

    print("Connecting to the server to download files…")

    with createSFTPClient(myHostname, myPort, myUsername, myPassword) as sftp:

        print("Connection established successfully.")
        sftp.chdir(remoteDir)

        # set up the progress bar here and pass it into the callback function
        with alive_bar(int(progress["total_size"]/1000000), bar = "smooth", spinner = "pointer", manual=True) as bar:
            
            current_item_number = 0

            for item in file_list:
           
                current_item_number += 1            # update stats
                progress["last_file_total"] = 0
                print("Downloading {} of {} | {} | {}".format(current_item_number,len(file_list),tidySize(item.size),item.name))
                
                local_path = targetDir + "/" + item.path

                # get local size if file exists; if it doesn't, create directories + download         
                if os.path.isfile(local_path):                   
                    local_size = os.stat(local_path).st_size
                    progress["total_down"] += local_size     # count already downloaded chunks
                else:
                    os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    local_size = 0
                
                remote_size = sftp.stat(item.path).st_size

                # download missing material (if it already exists, progress stats are updated above anyway)
                if local_size < remote_size:
                    with open(local_path, "ab") as local_file, sftp.open(item.path, "rb") as remote_file:
                        if local_size > 0:
                            remote_file.seek(local_size)
                        remote_file.prefetch(remote_size)
                        sftp._transfer_with_callback(reader=remote_file, writer=local_file, file_size=remote_size, callback=lambda x,y: updateProgress(x,bar))
                
            bar(1)    # make sure bar ends on 100% if we have iterated over whole list

# update the progress bar and track our total progress
def updateProgress(x,bar):
    
    time_change = time() - progress["last_time"]

    if time_change > 0.5:  # update the bar every 0.5 seconds

        progress["last_time"] = time()
    
        # calculate the progress
        data_change = x - progress["last_file_total"]
        progress["last_file_total"] = x
        progress["total_down"] += data_change
        percent = progress["total_down"]/progress["total_size"]   

        # calculate the momentary stats for speed/eta: unreliable when new files load, so need to average speeds
        # alive_progress ETA is inaccurate for restarted downloads, so we need to add our own
        speed = data_change/time_change
        progress["speed_series"].append(speed)
        speed_average = mean(progress["speed_series"])
        eta_seconds = (progress["total_size"] - progress["total_down"])/speed_average

        # hide extreme speed / eta
        if speed_average < 1000:    # 1 kb/s
            display_speed = "---"
        else:
            display_speed = tidySize(speed_average)

        if eta_seconds > 604800:    # 1 week
            display_eta = "---"
        else:
            display_eta = timedelta(seconds=int(eta_seconds))

        message = "{} / {} | {}/s | ETA: {}".format(tidySize(progress["total_down"]),tidySize(progress["total_size"]),display_speed,display_eta)
        bar.text(message)
        bar(percent)

# run the program
main()