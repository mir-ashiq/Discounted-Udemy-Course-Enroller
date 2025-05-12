import gradio as gr
import subprocess
import time

def run_app():
    process = subprocess.Popen(["python", "app.py"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    logs = ""
    while True:
        line = process.stdout.readline()
        if not line:
            break
        log_line = line.decode("utf-8")
        logs += log_line
        yield logs

    process.wait()
    if process.returncode != 0:
        logs += f"\nProcess exited with error code: {process.returncode}"
    yield logs

iface = gr.Interface(
    fn=run_app,
    inputs=None,
    outputs="text",
    title="Udemy Course Enroller",
    # live=True,
)

iface.launch(share=True)
