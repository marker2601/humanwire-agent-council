"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const token = document.querySelector('meta[name="humanwire-action-token"]');
  if (token === null) {
    return;
  }
  document.documentElement.dataset.studioReady = "true";
});
