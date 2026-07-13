const BASE = "/api/meetings";

let authToken = null;

export function setAuthToken(token) {
  authToken = token;
}

function getHeaders(extraHeaders = {}) {
  const headers = { ...extraHeaders };
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }
  return headers;
}

export function uploadMeeting(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file);

    xhr.open("POST", BASE);

    const headers = getHeaders();
    for (const [key, val] of Object.entries(headers)) {
      xhr.setRequestHeader(key, val);
    }

    if (xhr.upload && onProgress) {
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) {
          const percent = Math.round((e.loaded / e.total) * 100);
          onProgress(percent);
        }
      });
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (err) {
          resolve(xhr.responseText);
        }
      } else {
        try {
          const err = JSON.parse(xhr.responseText);
          reject(new Error(err.error?.message || err.detail || "Upload failed"));
        } catch (err) {
          reject(new Error(xhr.statusText || "Upload failed"));
        }
      }
    };

    xhr.onerror = () => {
      reject(new Error("Network error during upload"));
    };

    xhr.send(form);
  });
}

export async function getMeeting(id) {
  const res = await fetch(`${BASE}/${id}`, {
    headers: getHeaders()
  });
  if (!res.ok) throw new Error("Failed to fetch meeting");
  return res.json();
}

export async function listMeetings() {
  const res = await fetch(BASE, {
    headers: getHeaders()
  });
  if (!res.ok) throw new Error("Failed to fetch meetings");
  return res.json();
}

export async function deleteMeeting(id) {
  const res = await fetch(`${BASE}/${id}`, { 
    method: "DELETE",
    headers: getHeaders()
  });
  if (!res.ok) throw new Error("Failed to delete meeting");
}

export async function loginUser(email, password) {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password })
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Login failed" }));
    throw new Error(err.error?.message || err.detail || "Login failed");
  }
  const data = await res.json();
  setAuthToken(data.access_token);
  return data;
}

export async function registerUser(email, password) {
  const res = await fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password })
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Registration failed" }));
    throw new Error(err.error?.message || err.detail || "Registration failed");
  }
  const data = await res.json();
  setAuthToken(data.access_token);
  return data;
}
