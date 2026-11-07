// Send CSRF tokens only with same-origin requests that change state.
(() => {
  const token = document.querySelector('meta[name="csrf-token"]').content;
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, options = {}) => {
    const request = new Request(input, options);
    const url = new URL(request.url);
    if (url.origin === window.location.origin && !["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      request.headers.set("X-CSRF-Token", token);
    }
    return originalFetch(request);
  };
})();
