from flask import Flask, request
import yaml
import subprocess
import os
import tempfile

env = os.environ.copy()
env['GGCR_INSECURE'] = '1'
os.environ['GGCR_INSECURE'] = '1'


def logo():
    # Load and display the text file when the script starts
    with open("logo.txt", "r") as file:
        content = file.read()
        print(content)

app = Flask(__name__)

@app.route("/upload", methods=["POST"])
def upload_yaml():
    image_name = request.form.get("image_name", "apko-image")
    image_tag = request.form.get("image_tag", "apko-image")
    file = request.files["file"]
    tag = "superduper"

    # save uploaded YAML temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=".yaml") as tmp:
        file.save(tmp.name)
        yaml_path = tmp.name

    output_path = f"registry.localhost:5000/{image_name}:{image_tag}"

    # run apko build
    subprocess.run(
        [
            "apko", 
            "publish", 
            yaml_path,
            f"registry.localhost:5000/{image_name}:{image_tag}",
        ],
        check=True
    )
    print("-"*80)
    print("Built the following image: ", image_name, "TAG: ", image_tag)
    print("-"*80)
    return {"status": "built", "output": output_path}

if __name__ == "__main__":
    logo()
    app.run(host="0.0.0.0", port=8081)

