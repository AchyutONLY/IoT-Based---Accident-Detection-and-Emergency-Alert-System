"use client";

import { useEffect, useRef, useState } from "react";
import { Geolocation } from "@capacitor/geolocation";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function Home() {
  const [status, setStatus] = useState("LOADING");
  const [remainingSeconds, setRemainingSeconds] = useState(0);

  const [currentLatitude, setCurrentLatitude] = useState(null);
  const [currentLongitude, setCurrentLongitude] = useState(null);

  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const [connectionError, setConnectionError] = useState(null);

  const [deviceId, setDeviceId] = useState("c1");
  const [inputDeltaV, setInputDeltaV] = useState("");

  // --------------------------------------------------
  // LOCATION STATE
  // --------------------------------------------------

  const [locationStatus, setLocationStatus] =
    useState("NOT_REQUESTED");

  /*
    Accident ID for which location was successfully sent.
  */
  const locationSentAccidentRef = useRef(null);

  /*
    Prevent multiple GPS requests at the same time.
  */
  const locationRequestRunningRef = useRef(false);

  /*
    Time of last GPS attempt.
  */
  const lastLocationAttemptRef = useRef(0);

  /*
    Accident currently being handled.
  */
  const activeAccidentRef = useRef(null);

  /*
    Prevent repeated log fetching.
  */
  const logsFetchedAccidentRef = useRef(null);

  // --------------------------------------------------
  // ACCIDENT LOGS
  // --------------------------------------------------

  const [logs, setLogs] = useState([]);
  const [logsLoading, setLogsLoading] = useState(false);

  // ==================================================
  // GET PHONE LOCATION AND SEND TO BACKEND
  // ==================================================

  async function getAndSendPhoneLocation(accidentId) {
    if (!deviceId.trim()) {
      return;
    }

    if (!accidentId) {
      return;
    }

    /*
      If this accident already has a successfully
      uploaded location, do nothing.
    */
    if (
      locationSentAccidentRef.current === accidentId
    ) {
      return;
    }

    /*
      Prevent two GPS requests at the same time.
    */
    if (locationRequestRunningRef.current) {
      return;
    }

    /*
      Retry after a short delay if previous attempt failed.
    */
    const now = Date.now();

    if (
      now - lastLocationAttemptRef.current < 3000
    ) {
      return;
    }

    lastLocationAttemptRef.current = now;

    locationRequestRunningRef.current = true;

    setLocationStatus("REQUESTING");

    setMessage(
      "Accident detected. Getting driver's phone location..."
    );

    try {
      // ==================================================
      // CHECK LOCATION PERMISSION
      // ==================================================

      let permissions =
        await Geolocation.checkPermissions();

      console.log(
        "Location permissions:",
        permissions
      );

      /*
        We need precise location.

        If location permission is not granted,
        request it.
      */
      if (
        permissions.location !== "granted"
      ) {
        permissions =
          await Geolocation.requestPermissions();
      }

      console.log(
        "Location permissions after request:",
        permissions
      );

      /*
        Do NOT proceed unless precise location
        permission is granted.
      */
      if (
        permissions.location !== "granted"
      ) {
        setLocationStatus(
          "PERMISSION_DENIED"
        );

        setMessage(
          "Precise location permission is required. Please allow precise location for this app."
        );

        return;
      }

      // ==================================================
      // GET CURRENT PHONE LOCATION
      // ==================================================

      console.log(
        "Requesting current phone location..."
      );

      const position =
        await Geolocation.getCurrentPosition({
          enableHighAccuracy: true,

          /*
            Give GPS enough time to obtain a fix.
          */
          timeout: 15000,

          /*
            Force a fresh location.
          */
          maximumAge: 0,
        });

      console.log(
        "Complete GPS position:",
        position
      );

      const latitude =
        position.coords.latitude;

      const longitude =
        position.coords.longitude;

      console.log(
        "PHONE GPS:",
        latitude,
        longitude
      );

      // --------------------------------------------------
      // VALIDATE COORDINATES
      // --------------------------------------------------

      if (
        typeof latitude !== "number" ||
        typeof longitude !== "number" ||
        !Number.isFinite(latitude) ||
        !Number.isFinite(longitude)
      ) {
        throw new Error(
          "Phone returned invalid GPS coordinates."
        );
      }

      // --------------------------------------------------
      // UPDATE UI
      // --------------------------------------------------

      setCurrentLatitude(latitude);
      setCurrentLongitude(longitude);

      setLocationStatus(
        "LOCATION_FOUND"
      );

      setMessage(
        `Phone location found: ${latitude}, ${longitude}`
      );

      // ==================================================
      // SEND LOCATION TO BACKEND
      // ==================================================

      console.log(
        "Sending phone location to backend..."
      );

      const response =
        await fetch(
          `${API_URL}/api/accident/location/${encodeURIComponent(
            deviceId.trim()
          )}`,
          {
            method: "POST",

            headers: {
              "Content-Type":
                "application/json",
            },

            body: JSON.stringify({
              latitude,
              longitude,
            }),
          }
        );

      console.log(
        "Location upload HTTP status:",
        response.status
      );

      if (!response.ok) {
        const errorText =
          await response.text();

        throw new Error(
          `Location upload failed: HTTP ${response.status} ${errorText}`
        );
      }

      const data =
        await response.json();

      console.log(
        "Location backend response:",
        data
      );

      // --------------------------------------------------
      // SUCCESS
      // --------------------------------------------------

      locationSentAccidentRef.current =
        accidentId;

      setLocationStatus(
        "LOCATION_SENT"
      );

      setMessage(
        "Driver's phone location successfully attached to the accident."
      );

    } catch (error) {
      console.error(
        "ERROR GETTING/SENDING PHONE LOCATION:",
        error
      );

      setLocationStatus(
        "FAILED"
      );

      setMessage(
        `Unable to get phone location: ${
          error?.message || String(error)
        }`
      );

    } finally {
      locationRequestRunningRef.current =
        false;
    }
  }

  // ==================================================
  // CURRENT STATUS
  // ==================================================

  async function fetchStatus() {
    if (!deviceId.trim()) {
      return;
    }

    try {
      const response =
        await fetch(
          `${API_URL}/api/accident/status/${encodeURIComponent(
            deviceId
          )}`
        );

      if (!response.ok) {
        throw new Error(
          `HTTP error: ${response.status}`
        );
      }

      const data =
        await response.json();

      console.log(
        "STATUS:",
        data
      );

      // --------------------------------------------------
      // UPDATE STATUS
      // --------------------------------------------------

      setStatus(data.status);

      setRemainingSeconds(
        data.remaining_seconds ?? 0
      );

      // --------------------------------------------------
      // UPDATE LOCATION FROM BACKEND
      // --------------------------------------------------

      if (
        data.latitude !== null &&
        data.latitude !== undefined &&
        data.longitude !== null &&
        data.longitude !== undefined
      ) {
        setCurrentLatitude(
          data.latitude
        );

        setCurrentLongitude(
          data.longitude
        );
      }

      setConnectionError(null);

      // ==================================================
      // NEW ACCIDENT DETECTED
      // ==================================================

      if (
        data.accident_id &&
        (
          data.status === "COUNTDOWN" ||
          data.status === "ACCIDENT_CONFIRMED"
        )
      ) {
        /*
          Remember which accident is currently active.
        */
        activeAccidentRef.current =
          data.accident_id;

        /*
          If backend already has location,
          don't request GPS unnecessarily.
        */
        const backendAlreadyHasLocation =
          data.latitude !== null &&
          data.latitude !== undefined &&
          data.longitude !== null &&
          data.longitude !== undefined;

        /*
          If location is not already present,
          immediately try to obtain it.
        */
        if (
          !backendAlreadyHasLocation &&
          locationSentAccidentRef.current !==
            data.accident_id
        ) {
          getAndSendPhoneLocation(
            data.accident_id
          );
        }
      }

      // ==================================================
      // REFRESH LOGS AFTER ACCIDENT IS SAVED
      // ==================================================

      if (
        data.status === "ACCIDENT_CONFIRMED" &&
        data.log_saved === true &&
        data.accident_id &&
        logsFetchedAccidentRef.current !==
          data.accident_id
      ) {
        logsFetchedAccidentRef.current =
          data.accident_id;

        await fetchLogs();
      }

      // --------------------------------------------------
      // NORMAL STATE
      // --------------------------------------------------

      if (
        data.status === "NORMAL"
      ) {
        setLocationStatus(
          "NOT_REQUESTED"
        );
      }

    } catch (error) {
      console.error(
        "Error connecting to backend:",
        error
      );

      setStatus(
        "BACKEND_OFFLINE"
      );

      const errorDetail = {
        url: API_URL,
        type:
          error.name ||
          "Unknown",
        message:
          error.message ||
          String(error),
        timestamp:
          new Date().toLocaleString(),
      };

      setConnectionError(
        errorDetail
      );

      setMessage(
        `Connection failed: ${error.message}`
      );
    }
  }

  // ==================================================
  // GET LOGS
  // ==================================================

  async function fetchLogs() {
    if (!deviceId.trim()) {
      setLogs([]);
      return;
    }

    try {
      setLogsLoading(true);

      const response =
        await fetch(
          `${API_URL}/api/accident/logs/${encodeURIComponent(
            deviceId
          )}`
        );

      if (!response.ok) {
        throw new Error(
          `HTTP error: ${response.status}`
        );
      }

      const data =
        await response.json();

      setLogs(
        data.logs ?? []
      );

    } catch (error) {
      console.error(
        "Error fetching accident logs:",
        error
      );

      setLogs([]);

    } finally {
      setLogsLoading(false);
    }
  }

  // ==================================================
  // SIMULATE ACCIDENT
  // ==================================================

  async function triggerAccident() {
    if (!deviceId.trim()) {
      setMessage(
        "Please enter a vehicle ID."
      );
      return;
    }

    if (
      inputDeltaV.trim() === ""
    ) {
      setMessage(
        "Please enter change in velocity."
      );
      return;
    }

    const deltaV =
      Number(inputDeltaV);

    if (Number.isNaN(deltaV)) {
      setMessage(
        "Change in velocity must be a valid number."
      );
      return;
    }

    if (deltaV < 0) {
      setMessage(
        "Change in velocity cannot be negative."
      );
      return;
    }

    try {
      setLoading(true);
      setMessage("");

      const response =
        await fetch(
          `${API_URL}/api/accident/trigger`,
          {
            method: "POST",

            headers: {
              "Content-Type":
                "application/json",
            },

            body: JSON.stringify({
              device_id:
                deviceId.trim(),

              delta_v:
                deltaV,
            }),
          }
        );

      if (!response.ok) {
        throw new Error(
          `HTTP error: ${response.status}`
        );
      }

      const data =
        await response.json();

      console.log(
        "Manual accident response:",
        data
      );

      setMessage(
        data.message
      );

      /*
        If the backend returns the accident ID
        immediately, start GPS acquisition immediately.
      */
      if (
        data.accident_id
      ) {
        activeAccidentRef.current =
          data.accident_id;

        getAndSendPhoneLocation(
          data.accident_id
        );
      }

      await fetchStatus();

    } catch (error) {
      console.error(
        "Error triggering accident:",
        error
      );

      setConnectionError({
        url:
          `${API_URL}/api/accident/trigger`,

        type:
          error.name ||
          "Unknown",

        message:
          error.message ||
          String(error),

        timestamp:
          new Date().toLocaleString(),
      });

      setMessage(
        `Trigger failed: ${error.message}`
      );

    } finally {
      setLoading(false);
    }
  }

  // ==================================================
  // CANCEL ACCIDENT
  // ==================================================

  async function cancelAccident() {
    if (!deviceId.trim()) {
      return;
    }

    try {
      setLoading(true);
      setMessage("");

      const response =
        await fetch(
          `${API_URL}/api/accident/cancel/${encodeURIComponent(
            deviceId
          )}`,
          {
            method: "POST",
          }
        );

      if (!response.ok) {
        throw new Error(
          `HTTP error: ${response.status}`
        );
      }

      const data =
        await response.json();

      setMessage(
        data.message
      );

      await fetchStatus();

    } catch (error) {
      console.error(
        "Error cancelling accident:",
        error
      );

      setConnectionError({
        url:
          `${API_URL}/api/accident/cancel/${deviceId}`,

        type:
          error.name ||
          "Unknown",

        message:
          error.message ||
          String(error),

        timestamp:
          new Date().toLocaleString(),
      });

      setMessage(
        `Cancel failed: ${error.message}`
      );

    } finally {
      setLoading(false);
    }
  }

  // ==================================================
  // POLL STATUS
  // ==================================================

  useEffect(() => {
    fetchStatus();

    const interval =
      setInterval(() => {
        fetchStatus();
      }, 1000);

    return () => {
      clearInterval(interval);
    };
  }, [deviceId]);

  // ==================================================
  // FETCH LOGS WHEN DEVICE CHANGES
  // ==================================================

  useEffect(() => {
    fetchLogs();

    /*
      Reset accident-specific location state
      when changing vehicle.
    */
    locationSentAccidentRef.current =
      null;

    activeAccidentRef.current =
      null;

    setCurrentLatitude(null);
    setCurrentLongitude(null);
    setLocationStatus("NOT_REQUESTED");

  }, [deviceId]);

  // ==================================================
  // STATUS COLOR
  // ==================================================

  function getStatusColor() {
    if (status === "NORMAL") {
      return "bg-green-900/50 text-green-400 border border-green-700";
    }

    if (status === "COUNTDOWN") {
      return "bg-yellow-900/50 text-yellow-400 border border-yellow-700";
    }

    if (
      status === "ACCIDENT_CONFIRMED"
    ) {
      return "bg-red-900/50 text-red-400 border border-red-700";
    }

    if (status === "CANCELLED") {
      return "bg-blue-900/50 text-blue-400 border border-blue-700";
    }

    if (
      status === "BACKEND_OFFLINE"
    ) {
      return "bg-gray-800 text-gray-400 border border-gray-600";
    }

    return "bg-gray-800 text-gray-400 border border-gray-600";
  }

  // ==================================================
  // DISPLAY STATUS
  // ==================================================

  function getDisplayedStatus() {
    if (
      status === "ACCIDENT_CONFIRMED"
    ) {
      return "ACCIDENT DETECTED";
    }

    return status;
  }

  // ==================================================
  // STATUS TEXT
  // ==================================================

  function getStatusText() {
    if (status === "NORMAL") {
      return "Vehicle is operating normally";
    }

    if (status === "COUNTDOWN") {
      return "Possible accident detected";
    }

    if (
      status === "ACCIDENT_CONFIRMED"
    ) {
      return "ACCIDENT DETECTED. Emergency email and accident log processing completed.";
    }

    if (status === "CANCELLED") {
      return "Accident cancelled. Driver is okay.";
    }

    if (
      status === "BACKEND_OFFLINE"
    ) {
      return "Cannot connect to backend";
    }

    return "Loading vehicle status...";
  }

  // ==================================================
  // FORMAT TIMESTAMP
  // ==================================================

  function formatTimestamp(timestamp) {
    if (!timestamp) {
      return "N/A";
    }

    const date =
      new Date(timestamp);

    if (
      Number.isNaN(
        date.getTime()
      )
    ) {
      return timestamp;
    }

    return date.toLocaleString();
  }

  // ==================================================
  // UI
  // ==================================================

  return (
    <main className="min-h-screen bg-gray-950 p-4 sm:p-8">

      <div className="mx-auto max-w-7xl">

        <h1 className="mb-8 text-center text-3xl font-bold text-white sm:text-4xl">
          Accident Detection System
        </h1>

        <div className="rounded-xl bg-gray-900 p-6 shadow-lg ring-1 ring-gray-800 sm:p-8">

          {/* VEHICLE INFORMATION */}

          <h2 className="mb-6 text-2xl font-semibold text-white">
            Vehicle Information
          </h2>

          <div className="mb-8 grid grid-cols-1 gap-5 md:grid-cols-2">

            <div>
              <label className="mb-2 block text-sm font-medium text-gray-400">
                Vehicle ID
              </label>

              <input
                type="text"
                value={deviceId}
                onChange={(e) =>
                  setDeviceId(
                    e.target.value
                  )
                }
                disabled={
                  status === "COUNTDOWN"
                }
                placeholder="e.g. c1"
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-4 py-3 text-white placeholder:text-gray-500 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50"
              />
            </div>

            <div>
              <label className="mb-2 block text-sm font-medium text-gray-400">
                Change in Velocity (Δv)
              </label>

              <input
                type="number"
                step="any"
                value={inputDeltaV}
                onChange={(e) =>
                  setInputDeltaV(
                    e.target.value
                  )
                }
                disabled={
                  status === "COUNTDOWN"
                }
                placeholder="Used only for simulation"
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-4 py-3 text-white placeholder:text-gray-500 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50"
              />

              <p className="mt-2 text-xs text-gray-500">
                During real operation, Δv comes from the ESP32.
              </p>
            </div>

          </div>

          {/* PHONE LOCATION STATUS */}

          <div className="mb-6 rounded-lg bg-gray-800 p-5 ring-1 ring-gray-700">

            <p className="text-sm text-gray-400">
              Driver Phone Location
            </p>

            {locationStatus === "REQUESTING" && (
              <p className="mt-2 text-yellow-400">
                Getting current phone location...
              </p>
            )}

            {locationStatus === "LOCATION_FOUND" && (
              <p className="mt-2 text-blue-400">
                GPS location obtained. Sending to backend...
              </p>
            )}

            {locationStatus === "LOCATION_SENT" && (
              <p className="mt-2 text-green-400">
                Phone location attached to current accident
              </p>
            )}

            {locationStatus === "PERMISSION_DENIED" && (
              <p className="mt-2 text-red-400">
                Precise location permission is required.
              </p>
            )}

            {locationStatus === "FAILED" && (
              <p className="mt-2 text-red-400">
                Unable to obtain phone location. Retrying...
              </p>
            )}

            {locationStatus === "NOT_REQUESTED" && (
              <p className="mt-2 text-gray-500">
                Location will be requested automatically when an accident is detected.
              </p>
            )}

          </div>

          {/* CURRENT STATUS */}

          <h2 className="mb-6 text-2xl font-semibold text-white">
            Vehicle Status
          </h2>

          <div
            className={`mb-6 overflow-hidden rounded-lg p-6 text-center ${getStatusColor()}`}
          >
            <p className="break-words text-xl font-bold sm:text-3xl">
              {getDisplayedStatus()}
            </p>

            <p className="mt-2">
              {getStatusText()}
            </p>
          </div>

          {/* SIMULATE ACCIDENT */}

          {(status === "NORMAL" ||
            status === "CANCELLED") && (

            <div className="mb-6 text-center">

              <button
                onClick={triggerAccident}
                disabled={loading}
                className="rounded-lg bg-orange-600 px-8 py-4 text-lg font-bold text-white hover:bg-orange-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {loading
                  ? "TRIGGERING..."
                  : "SIMULATE ACCIDENT"}
              </button>

            </div>
          )}

          {/* COUNTDOWN */}

          {status === "COUNTDOWN" && (
            <>
              <div className="mb-6 rounded-lg bg-orange-900/40 p-6 text-center ring-1 ring-orange-700">

                <p className="text-lg font-medium text-orange-300">
                  POSSIBLE ACCIDENT
                </p>

                <p className="mt-2 text-7xl font-bold text-orange-500">
                  {remainingSeconds}
                </p>

                <p className="mt-2 text-orange-400/70">
                  seconds remaining
                </p>

                <p className="mt-4 text-sm text-gray-400">
                  The phone is automatically providing the driver's location.
                </p>

              </div>

              <div className="mb-6 text-center">

                <button
                  onClick={cancelAccident}
                  disabled={loading}
                  className="rounded-lg bg-red-600 px-8 py-4 text-lg font-bold text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {loading
                    ? "CANCELLING..."
                    : "CANCEL ACCIDENT"}
                </button>

              </div>
            </>
          )}

          {/* MESSAGE */}

          {message && (
            <div className="mb-6 rounded-lg bg-blue-900/40 p-4 text-center text-blue-400 ring-1 ring-blue-700">
              {message}
            </div>
          )}

          {/* CURRENT VEHICLE DATA */}

          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">

            <div className="rounded-lg bg-gray-800 p-5 ring-1 ring-gray-700">

              <p className="text-sm text-gray-400">
                Vehicle ID
              </p>

              <p className="mt-1 text-xl font-semibold text-white">
                {deviceId || "Not selected"}
              </p>

            </div>

            <div className="rounded-lg bg-gray-800 p-5 ring-1 ring-gray-700">

              <p className="text-sm text-gray-400">
                GPS Location
              </p>

              {currentLatitude !== null &&
              currentLongitude !== null ? (
                <>
                  <p className="mt-1 text-lg font-semibold text-white">
                    {currentLatitude},{" "}
                    {currentLongitude}
                  </p>

                  <p className="mt-2 text-xs text-green-400">
                    Source: Driver's phone
                  </p>
                </>
              ) : (
                <p className="mt-1 text-xl font-semibold text-white">
                  Not available
                </p>
              )}

            </div>

            <div className="rounded-lg bg-gray-800 p-5 ring-1 ring-gray-700">

              <p className="text-sm text-gray-400">
                Total Recorded Accidents
              </p>

              <p className="mt-1 text-xl font-semibold text-white">
                {logs.length}
              </p>

            </div>

          </div>

          {/* GOOGLE MAPS */}

          {currentLatitude !== null &&
          currentLongitude !== null && (

            <div className="mt-6 text-center">

              <a
                href={`https://www.google.com/maps?q=${currentLatitude},${currentLongitude}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block rounded-lg bg-blue-600 px-6 py-3 font-semibold text-white hover:bg-blue-500"
              >
                Open Location in Google Maps
              </a>

            </div>
          )}

        </div>

        {/* CONNECTION DEBUG PANEL */}

        {connectionError && (

          <div className="mt-8 rounded-xl border-2 border-red-800 bg-red-950 p-6 shadow-lg">

            <div className="mb-4 flex items-center justify-between">

              <h2 className="text-xl font-bold text-red-400">
                Connection Debug Info
              </h2>

              <button
                onClick={() =>
                  setConnectionError(null)
                }
                className="rounded bg-red-900 px-3 py-1 text-sm font-semibold text-red-400 hover:bg-red-800"
              >
                Dismiss
              </button>

            </div>

            <div className="space-y-3">

              <div className="rounded-lg bg-gray-900 p-4 ring-1 ring-gray-700">

                <p className="text-sm font-semibold text-gray-400">
                  Backend URL
                </p>

                <p className="mt-1 break-all font-mono text-base text-red-400">
                  {connectionError.url}
                </p>

              </div>

              <div className="rounded-lg bg-gray-900 p-4 ring-1 ring-gray-700">

                <p className="text-sm font-semibold text-gray-400">
                  Error Type
                </p>

                <p className="mt-1 font-mono text-base text-red-400">
                  {connectionError.type}
                </p>

              </div>

              <div className="rounded-lg bg-gray-900 p-4 ring-1 ring-gray-700">

                <p className="text-sm font-semibold text-gray-400">
                  Error Message
                </p>

                <p className="mt-1 break-all font-mono text-sm text-red-400">
                  {connectionError.message}
                </p>

              </div>

              <div className="rounded-lg bg-gray-900 p-4 ring-1 ring-gray-700">

                <p className="text-sm font-semibold text-gray-400">
                  Time
                </p>

                <p className="mt-1 text-base text-gray-300">
                  {connectionError.timestamp}
                </p>

              </div>

              <div className="rounded-lg bg-yellow-900/30 p-4 ring-1 ring-yellow-700">

                <p className="mb-2 text-sm font-bold text-yellow-400">
                  Common Fixes:
                </p>

                <ul className="space-y-1 text-sm text-yellow-300/80">

                  <li>
                    1. Make sure backend is running.
                  </li>

                  <li>
                    2. Phone and Raspberry Pi must be on the same Wi-Fi network.
                  </li>

                  <li>
                    3. Check firewall is not blocking port 8000.
                  </li>

                  <li>
                    4. Verify NEXT_PUBLIC_API_URL matches the Raspberry Pi IP.
                  </li>

                  <li>
                    5. Run hostname -I on the Raspberry Pi to get its current IP.
                  </li>

                  <li>
                    6. After changing .env.local:
                    npm run build && npx cap sync android
                  </li>

                </ul>

              </div>

            </div>

          </div>
        )}

        {/* ACCIDENT LOGS */}

        <div className="mt-8 rounded-xl bg-gray-900 p-6 shadow-lg ring-1 ring-gray-800 sm:p-8">

          <div className="mb-6 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">

            <div>

              <h2 className="text-2xl font-semibold text-white">
                Accident Logs
              </h2>

              <p className="mt-1 text-sm text-gray-400">
                Accident history for vehicle{" "}
                <span className="font-semibold text-gray-300">
                  {deviceId}
                </span>
              </p>

            </div>

            <button
              onClick={fetchLogs}
              disabled={logsLoading}
              className="rounded-lg bg-gray-700 px-5 py-2 font-semibold text-white hover:bg-gray-600 disabled:opacity-50"
            >
              {logsLoading
                ? "Loading..."
                : "Refresh Logs"}
            </button>

          </div>

          {logsLoading ? (

            <div className="py-10 text-center text-gray-400">
              Loading accident logs...
            </div>

          ) : logs.length === 0 ? (

            <div className="rounded-lg bg-gray-800 py-10 text-center text-gray-400">
              No accident records found for {deviceId}.
            </div>

          ) : (

            <div className="overflow-x-auto">

              <table className="w-full border-collapse text-left">

                <thead>

                  <tr className="border-b border-gray-700 bg-gray-800">

                    <th className="px-4 py-3 text-sm font-semibold text-gray-300">
                      #
                    </th>

                    <th className="px-4 py-3 text-sm font-semibold text-gray-300">
                      Accident ID
                    </th>

                    <th className="px-4 py-3 text-sm font-semibold text-gray-300">
                      Δv (m/s)
                    </th>

                    <th className="px-4 py-3 text-sm font-semibold text-gray-300">
                      Timestamp
                    </th>

                    <th className="px-4 py-3 text-sm font-semibold text-gray-300">
                      Latitude
                    </th>

                    <th className="px-4 py-3 text-sm font-semibold text-gray-300">
                      Longitude
                    </th>

                    <th className="px-4 py-3 text-sm font-semibold text-gray-300">
                      Email
                    </th>

                    <th className="px-4 py-3 text-sm font-semibold text-gray-300">
                      Location
                    </th>

                  </tr>

                </thead>

                <tbody>

                  {logs.map(
                    (log, index) => (

                      <tr
                        key={
                          log.accident_id ??
                          log.id
                        }
                        className="border-b border-gray-800 hover:bg-gray-800/50"
                      >

                        <td className="px-4 py-4 text-sm text-gray-300">
                          {index + 1}
                        </td>

                        <td className="px-4 py-4 text-xs font-mono text-gray-300">
                          {log.accident_id}
                        </td>

                        <td className="px-4 py-4 text-sm font-semibold text-white">
                          {Number(
                            log.delta_v
                          ).toFixed(2)}
                        </td>

                        <td className="whitespace-nowrap px-4 py-4 text-sm text-gray-300">
                          {formatTimestamp(
                            log.timestamp
                          )}
                        </td>

                        <td className="px-4 py-4 text-sm text-gray-300">
                          {log.latitude ?? "N/A"}
                        </td>

                        <td className="px-4 py-4 text-sm text-gray-300">
                          {log.longitude ?? "N/A"}
                        </td>

                        <td className="px-4 py-4 text-sm">

                          {log.email_sent ? (

                            <span className="rounded-full bg-green-900/50 px-3 py-1 text-xs font-semibold text-green-400 ring-1 ring-green-700">
                              Sent
                            </span>

                          ) : (

                            <span className="rounded-full bg-red-900/50 px-3 py-1 text-xs font-semibold text-red-400 ring-1 ring-red-700">
                              Failed
                            </span>

                          )}

                        </td>

                        <td className="px-4 py-4 text-sm">

                          {log.google_maps_link ||
                          (
                            log.latitude !== null &&
                            log.latitude !== undefined &&
                            log.longitude !== null &&
                            log.longitude !== undefined
                          ) ? (

                            <a
                              href={
                                log.google_maps_link ||
                                `https://www.google.com/maps?q=${log.latitude},${log.longitude}`
                              }
                              target="_blank"
                              rel="noopener noreferrer"
                              className="font-semibold text-blue-400 hover:text-blue-300 hover:underline"
                            >
                              View Map
                            </a>

                          ) : (

                            <span className="text-gray-500">
                              Location unavailable
                            </span>

                          )}

                        </td>

                      </tr>
                    )
                  )}

                </tbody>

              </table>

            </div>
          )}

        </div>

      </div>

    </main>
  );
}