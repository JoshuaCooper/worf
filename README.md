## W.O.R.F. – Wolfi Offline Release Framework

An experimental project created to deepen my understanding of APKO and Melange.

This system uses APKO, Melange, and an APK server to build container images for local use in offline or restricted environments.

### APK-Sever: 
Service for serving files built by Melange, caching and reverse proxying WolfiOS repositories and hosting APKs built locally.

### APKO-Server 
System for receiving APKO files and creating and pushing to the local registry.

### Registry 
System for storing images built by APKO-Server. 

### Customizer [NOT STARTED]
A user interface for building custom APKO files to then push to APKO-Server via curl. This interface will build and publish your image to the registry container.

### Offline Mode [NOT STARTED]
The goal is be able to take this into a system/location without network resources and continue to deploy your applications. 

### Getting started
The WORF system is entered via `make start`, this will execute the initial build leveraging ChainGuard's APKO image to initialize the first image, then creating a custom another APKO image with python so we can start 

Start WORF:
<pre>make start</pre>

Then use APKO-Server to start building out your images. 

- Example:

<pre>
curl -X POST \
    -F "image_name=IMAGENAME" \
    -F "image_tag=IMAGETAG" \
    -F "file=@example_apko_file.yaml" \
    http://localhost:8081/upload
</pre>



