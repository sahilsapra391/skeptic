"use client";

/**
 * Speech-to-text for the strategy composer, on the browser's native
 * SpeechRecognition (Chrome/Edge/Safari — streamed recognition, no API key,
 * no audio passing through our backend).
 *
 * Built for dictating a strategy in one go: continuous mode with interim
 * results streaming into the composer, and automatic restart when the
 * engine times out on silence mid-thought — the mic stays hot until the
 * user explicitly stops.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { polishDictation } from "@/lib/dictation";

// lib.dom omits the Web Speech API; minimal typings for what we use
interface SpeechAlternative {
  transcript: string;
}
interface SpeechResult {
  isFinal: boolean;
  0: SpeechAlternative;
}
interface SpeechResultEvent {
  resultIndex: number;
  results: { length: number; [i: number]: SpeechResult };
}
interface SpeechErrorEvent {
  error: string;
}
interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: SpeechResultEvent) => void) | null;
  onerror: ((e: SpeechErrorEvent) => void) | null;
  onend: (() => void) | null;
}

function getRecognitionCtor(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as Record<string, unknown>;
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null) as
    | (new () => SpeechRecognitionLike)
    | null;
}

export interface SpeechState {
  supported: boolean;
  listening: boolean;
  interim: string;
  error: string | null;
  start: () => void;
  stop: () => void;
}

/** onSegment receives each finalized utterance, whitespace-normalized.
 * The caller owns joining/capitalization against its existing text. */
export function useSpeechToText(onSegment: (segment: string) => void): SpeechState {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);

  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const activeRef = useRef(false); // user intent — survives engine auto-stops
  const onSegmentRef = useRef(onSegment);
  onSegmentRef.current = onSegment;

  useEffect(() => {
    setSupported(getRecognitionCtor() != null);
    return () => {
      activeRef.current = false;
      recRef.current?.abort();
    };
  }, []);

  const spinUpRef = useRef<() => void>(() => undefined);
  spinUpRef.current = () => {
    const Ctor = getRecognitionCtor();
    if (!Ctor) return;
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-US";

    rec.onresult = (e) => {
      let interimText = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        const transcript = r[0]?.transcript ?? "";
        if (r.isFinal) {
          // polish rewrites prose-style transcription into strategy-speak:
          // digits for spoken numbers, canonical tickers, no stray caps
          const cleaned = polishDictation(transcript);
          if (cleaned) onSegmentRef.current(cleaned);
        } else {
          interimText += transcript;
        }
      }
      setInterim(polishDictation(interimText));
    };

    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        setError("microphone permission denied — allow it in the address bar");
        activeRef.current = false;
        setListening(false);
      }
      // "no-speech"/"aborted" are normal silence timeouts; onend restarts
    };

    rec.onend = () => {
      setInterim("");
      if (activeRef.current) {
        try {
          spinUpRef.current();
        } catch {
          activeRef.current = false;
          setListening(false);
        }
      } else {
        setListening(false);
      }
    };

    recRef.current = rec;
    rec.start();
  };

  const start = useCallback(() => {
    if (activeRef.current) return;
    setError(null);
    activeRef.current = true;
    setListening(true);
    try {
      spinUpRef.current();
    } catch {
      setError("voice input failed to start");
      activeRef.current = false;
      setListening(false);
    }
  }, []);

  const stop = useCallback(() => {
    activeRef.current = false;
    setListening(false);
    setInterim("");
    recRef.current?.stop();
  }, []);

  return { supported, listening, interim, error, start, stop };
}
