let token = null;

export const api = {
  setToken(value) {
    token = value;
  },
  async request(path, options = {}) {
    const headers = { ...options.headers };
    if (token) headers.Authorization = `Bearer ${token}`;
    let body = options.body;
    if (body !== undefined && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const response = await fetch(path, { ...options, headers, body });
    if (!response.ok) {
      let detail;
      try {
        detail = (await response.json()).detail;
      } catch {
        detail = "Сервер временно недоступен";
      }
      if (Array.isArray(detail))
        detail = detail.map((x) => `${x.loc.at(-1)}: ${x.msg}`).join("\n");
      const error = new Error(
        detail || `Не удалось выполнить действие (${response.status})`,
      );
      error.status = response.status;
      if (response.status === 401 && !path.includes("/auth/"))
        window.dispatchEvent(new Event("session-expired"));
      throw error;
    }
    return response.status === 204 ? null : response.json();
  },
  get(path) {
    return this.request(path);
  },
  post(path, body, headers) {
    return this.request(path, { method: "POST", body, headers });
  },
  put(path, body) {
    return this.request(path, { method: "PUT", body });
  },
  delete(path) {
    return this.request(path, { method: "DELETE" });
  },
  async downloadFile(path, filename) {
    if (window.WebApp?.initData && window.WebApp?.downloadFile) {
      await window.WebApp.downloadFile(
        new URL(path, location.origin).href,
        filename,
      );
      return;
    }
    const response = await fetch(path);
    if (!response.ok)
      throw new Error("Ссылка истекла. Сформируйте выгрузку заново");
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  async download(id, filename) {
    if (window.WebApp?.initData && window.WebApp?.downloadFile) {
      const link = await this.post(
        `/api/documents/${encodeURIComponent(id)}/download-link`,
      );
      await window.WebApp.downloadFile(link.url, filename);
      return;
    }
    const response = await fetch(
      `/api/documents/${encodeURIComponent(id)}/download`,
      {
        headers: { Authorization: `Bearer ${token}` },
      },
    );
    if (!response.ok)
      throw new Error("Файл недоступен. Обновите список документов");
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
