import os
from multiprocessing import Process, Manager
from ctypes import c_char_p
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
    def __eq__(self, other):                   # control for items already in queue
        return self.__dict__ == other.__dict__

# main function: connect and then select the files to download
# actual downloads create a new SFTP connection in order to resume on break
def main():

    # remove these two lines if specifying targetDir from config
    global targetDir
    targetDir = input("Drag and drop to select target local directory:\n").replace("\\", "").strip()
    
    print("Connecting to the server…")

    # collect downloads + any notifications/errors
    global downloads_list
    downloads_list = []
    added = ""
    error = ""
    global already_added
    already_added = ""

    with createSFTPClient(myHostname, myPort, myUsername, myPassword) as sftp:

        print("Connection established successfully.")
        sftp.chdir(remoteDir)
        global remoteDirFull
        remoteDirFull = sftp.getcwd()   # get the full path; used for truncating local filepaths later

        # display the current selected directory then loop to get user input and choose what to do next; break loop to initiate downloads
        while(True):

            # list the directory, give update on the download list and print any errors/updates
            # catch the permission error here at start of loop
            try:
                directory_structure = sftp.listdir_attr()
            except Exception as e:
                error = "\nError changing directory: " + str(e) + "\nReturning to main directory."
                sftp.chdir(remoteDirFull)
                continue

            directory_structure.sort(key = lambda x: x.st_mtime, reverse=True)  # sort by date, newest first
            print("\nCurrent directory contents:")
            for x in reversed(directory_structure):                             
                print(directory_structure.index(x),"|",getSize(x),"|", x.filename)
            print("\nCurrent directory path:",sftp.getcwd())
            print("Downloads list: {} items, {} total".format(len(downloads_list),tidySize(sum(x.size for x in downloads_list))))

            if added != "":
                print(added)
                added = ""
            if already_added != "":
                print(already_added)
                already_added = ""
            if error != "":
                print(error)
                error = ""

            # process the user's input
            nav_choice = input("\n\"cd\": change directory. \"add\": add to download queue. \"clear\": clear queue. \"dl\": start downloading.\nExample commands: \"cd 12\", \"cd ..\", \"add 2\", \"add 1,3,5\", \"add all\", \"dl\", \"exit\".\n")

            try:

                if nav_choice[:3] == "cd ":                     # change directory
                    if nav_choice == "cd ..":
                        sftp.chdir("..")
                    else:
                        index = int(nav_choice[3:])
                        sftp.chdir(directory_structure[index].filename)

                elif nav_choice[:4] == "add ":                   # add files to list
                    root_folders = [] 
                    if nav_choice.strip() == "add all":
                        indices = range(0,len(directory_structure))
                    else:
                        indices = nav_choice[4:].split(",")
                    added = "\n{} file(s)/folder(s) added:".format(len(indices))
                    for index in indices:
                        root_folders.append(directory_structure[int(index)].filename)
                        added += "\n" + directory_structure[int(index)].filename
                    print("Adding {} file(s)/folder(s) to download queue…".format(len(indices)))
                    for item in root_folders:
                        print(item)
                        getFileInfo(item,"",downloads_list,sftp)  # pass on empty base path + sftp client                        

                elif nav_choice.strip() == "clear":               # clear downloads
                    print("Clearing downloads list…")
                    downloads_list = []
                    sleep(1)

                elif nav_choice.strip() == "dl":                  # initiate downloads
                    break

                elif nav_choice.strip() == "exit":                # quit
                    exit(0)

                else:
                    error = "\nCommand not recognized: \"" + nav_choice + "\""

            except Exception as e:
                error = "\nError during input: " + str(e)
                continue
        
        # initiate downloads
        downloadLoop()

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
            return size_string + " " * diff
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
            getFileInfo(x,file_path + "/",target_list,sftp_client)  # turn filepath into new base bath

# create a new "File" object and add it to the downloads list
def addFileToList(filename,file_path,target_list,sftp_client):

    global already_added

    new_file = File(
        filename,
        sftp_client.getcwd() + "/" + file_path,
        sftp_client.stat(file_path).st_size
    )

    for x in target_list:
        if x == new_file:
            already_added += "\nAlready in queue: " + new_file.name
            break
    else:
        target_list.append(new_file)

# create a new process for downloading files; loop to check download progress
# if progress is stalled, kill the process and restart downloads
def downloadLoop():

    # store variables modified by the process here + pass in as args when creating it
    manager = Manager()
    download_status = manager.Value("i", 1)
    download_current_file = manager.Value(c_char_p, "")
    p = Process(target=download, args=[download_status,download_current_file])
    p.start()

    # wait for a first file to be fetched
    while(download_current_file.value == ""):
        sleep(1)

    last_file = download_current_file.value
    last_size = os.stat(last_file).st_size
    
    while bool(download_status.value):
        
        sleep(5)
        current_file = download_current_file.value
        current_size = os.stat(current_file).st_size

        # if file hasn't changed, kill the process
        if last_file == current_file and last_size == current_size:       
            p.terminate()
            p.join()
            print("Connection error. Restarting download…")
            p = Process(target=download, args=[download_status,download_current_file])
            p.start()

        # update last file info before looping again
        last_file = current_file
        last_size = current_size
    
    exit(0)
        
# download all the files in the list, creating a new SFTP client each time the function runs
def download(status,current_file):

    # track progress: make these properties global to manipulate with the callback function
    global progress
    progress = {
        "total_size": sum(x.size for x in downloads_list),  # get values from main unmodified list
        "last_file_total": 0,
        "total_down": 0,
        "last_time": 0,
        "speed_series": deque([],maxlen=40),     # store the last 40 speeds to give 20 sec smoother average
    }

    print("Connecting to the server to download files…")

    # if paramiko produces an error, just return and let the loop execute again
    try:
        with createSFTPClient(myHostname, myPort, myUsername, myPassword) as sftp:

            print("Connection established successfully.")

            # set up the progress bar here and pass it into the callback function
            with alive_bar(int(progress["total_size"]/1000000), bar = "smooth", spinner = "pointer", manual=True) as bar:
        
                current_item_number = 0

                for item in downloads_list:
            
                    current_item_number += 1            # update stats
                    progress["last_file_total"] = 0
                    print("Downloading {} of {} | {} | {}".format(current_item_number,len(downloads_list),tidySize(item.size),item.name))
                    
                    local_path = targetDir + tidyPath(item.path)
                    current_file.value = local_path # modify shared value for checking progress

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
    except:
        return

    # if all downloads actually finished, modify shared value to break loop
    status.value = 0
    print("All downloads finished.")

# remove the unwanted parts of the remoteDir path 
def tidyPath(remote_path):

    diff = False
    for i in range(0,len(remoteDirFull)):
        if remoteDirFull[i] != remote_path[i]:
            cut = i
            diff = True
            break
    if diff == False:               # catch the case where the whole string is contained
        cut = len(remoteDirFull)    # NB. these will contain "/", others won't

    if cut == 0:                    # find the first relevant "/" in the string
        return remote_path
    else:
        first_half = remote_path[:cut+1]
        dir_begin = 0
        for i in range(len(first_half)-1,-1,-1):
            if first_half[i] == "/":
                dir_begin = i
                break
        return remote_path[dir_begin:]

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