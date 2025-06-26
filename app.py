from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    AgentWebSocketEvents,
    SettingsOptions,
    FunctionCallRequest,
    FunctionCallResponse,
    Input,
    Output,
    PrerecordedOptions,
    FileSource
)
import os
import json
import openai
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
from flask_cors import CORS
from werkzeug.utils import secure_filename
import uuid

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", path='/socket.io')

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

user_sessions = {}



client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

saved_transcripts = []

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html', transcripts=saved_transcripts)


def transcribe_audio(audio_file):
    deepgram = DeepgramClient(os.getenv("DEEPGRAM_API_KEY", ""))

    with open(audio_file, "rb") as file:
        buffer_data = file.read()

    payload: FileSource = {
        "buffer": buffer_data,
    }

    options = PrerecordedOptions(
        model="nova-3-medical",
        smart_format=True,
        diarize=True
    )

    response = deepgram.listen.prerecorded.v("1").transcribe_file(payload, options)

    # Access structured fields directly
    paragraphs = []
    try:
        alternatives = response.results.channels[0].alternatives[0]
        if hasattr(alternatives, "paragraphs") and hasattr(alternatives.paragraphs, "paragraphs"):
            paragraphs = alternatives.paragraphs.paragraphs
    except Exception as e:
        print(f"Error extracting paragraphs: {e}")

    transcript = []
    for para in paragraphs:
        speaker = getattr(para, "speaker", "unknown")
        content = " ".join(sentence.text for sentence in para.sentences)
        transcript.append({"role": speaker, "content": content})

    return transcript


@app.route('/upload_scribe', methods=['POST'])
def upload_scribe():
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file uploaded'}), 400



    file = request.files['file']
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    transcript = transcribe_audio(filepath)
    session_id = request.args.get('session_id', str(uuid.uuid4()))
    user_sessions.setdefault(session_id, {'scribe': []})

    user_sessions[session_id]['scribe'] += transcript

    socketio.emit("scribe_transcript", {"transcript": transcript, "session_id": session_id})
    return jsonify({"status": "success", "transcript": transcript})


@socketio.on('connect')
def on_connect():
    print("✅ Socket connected")
    

@socketio.on('stop_recording')
def on_disconnect():
    print("❌ Socket disconnected")



@socketio.on("reset_dashboard")
def handle_reset_dashboard(data):
    session_id = data.get("session_id")
    print(f"🔁 Resetting dashboard for session: {session_id}")
    socketio.emit("reset_dashboard", {"session_id": session_id})

@socketio.on('summarize')
def handle_summarize(data):
    session_id = data.get("session_id")
    print("🧠 Summarize event received with data:", data)
    transcript = data.get('transcript', '')

    system_prompt = (
        "You are a medical scribe. Summarize the provided transcripts into a formal structured note for a orthopedic trauma consult. IF information is not available do not hallucinate, instead report not assessed"
        "Specify laterality in the chief complaint (e.g., left, bilateral right worse than left, etc)."
        "include details such as dates, types, providers, and relief. Extract and format in html with formatting, no title needed, omit markdown rendering tags, Do not wrap section headers in angle brackets (<>). Use valid HTML tags only. i am inputting this summary to a webapp."
        "try to make it as similar fitting to this template as possible: " 
        """Chief Complaint:
[]

HPI:
[]

Past medical history, past surgical history, family history, medications, allergies, and review of systems are reviewed on the comprehensive intake form which is scanned into the chart, notable as above.

Social History:
   - Smoking status: 
   - EtOH:
   - Ambulatory status:
   - Employment: 

Physical exam:
[]

Imaging:
[ ] radiographs are obtained and reviewed with the patient. They demonstrate [].

Assessment and plan:
[]

Diagnosis and treatment options discussed with the patient. []
 
The patient is in agreement with the treatment plan and all of their questions were answered."""

    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcript}
        ],
        temperature=0.3,
    )

    summary = response.choices[0].message.content
    socketio.emit('summary', {'summary': summary, 'session_id': session_id})


@socketio.on('summarizeFU')
def handle_summarize(data):
    session_id = data.get("session_id")
    print("🧠 Summarize follow-up received with data:", data)
    transcript = data.get('transcript', '')

    system_prompt = (
        "You are a medical scribe. Summarize the provided transcripts into a follow-up progress note with the format of "
        """CHIEF COMPLAINT:
[]
 
DATE OF SURGERY:
[]
 
HISTORY OF PRESENT ILLNESS:
[]
 
Past medical history, past surgical history, family history, medications, allergies, and review of systems are reviewed on the comprehensive intake form which is scanned into the chart, notable as above.
 
SOCIAL HISTORY:
   - Smoking status: 
   - EtOH:
   - Ambulatory status:
   - Employment: 
 
PHYSICAL EXAMINATION:
[]
 
IMAGING:
[] radiographs are obtained and reviewed with the patient. They demonstrate [].
 
ASSESSMENT AND PLAN:
[]

We reviewed the findings. []

The patient is in agreement with the treatment plan and all of their questions were answered."""
        ". IF information is not available do not hallucinate, instead report unknown"
        " Extract and format in html with formatting, no title needed, omit markdown rendering tags,Do not wrap section headers in angle brackets (<>). Use valid HTML tags only.  i am inputting this summary to a webapp."

    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcript}
        ],
        temperature=0.3,
    )

    summary = response.choices[0].message.content
    socketio.emit('summary', {'summary': summary, 'session_id': session_id})


if __name__ == '__main__':
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True, port=3000, host='0.0.0.0')
