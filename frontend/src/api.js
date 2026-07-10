const BASE = "/api/meetings";

export async function uploadMeeting(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(BASE, { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Upload failed");
  }
  return res.json();
}

export async function getMeeting(id) {
  const res = await fetch(`${BASE}/${id}`);
  if (!res.ok) throw new Error("Failed to fetch meeting");
  return res.json();
}

export async function listMeetings() {
  const res = await fetch(BASE);
  if (!res.ok) throw new Error("Failed to fetch meetings");
  return res.json();
}

export async function deleteMeeting(id) {
  const res = await fetch(`${BASE}/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete meeting");
}
