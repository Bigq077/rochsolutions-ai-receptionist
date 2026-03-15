# app/media_streams/__init__.py
"""
Media Streams parallel voice pipeline for Susie AI receptionist.

This package implements a parallel, chunked streaming architecture:
  Twilio Media Streams (WebSocket) -> AssemblyAI STT -> Claude LLM -> ElevenLabs TTS

Architecture overview:
  - connection.py   : WebSocket lifecycle management
  - audio_in.py     : Inbound Twilio audio handling and decoding
  - audio_out.py    : Outbound TTS audio encoding and Twilio delivery
  - stt_stream.py   : AssemblyAI streaming STT connection and events
  - llm_stream.py   : Claude streaming LLM with chunked output
  - tts_stream.py   : ElevenLabs streaming TTS
  - chunker.py      : Text chunking logic for streaming TTS
  - fast_path.py    : Fast-path pattern matching (re-uses app.fast_path)
  - session.py      : Session storage with Redis (ms_session: prefix)
  - router.py       : FastAPI WebSocket route registration
  - config.py       : All constants and environment variables
"""
