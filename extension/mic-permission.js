// Visible page — Chrome will show the mic permission dialog here.
// Requests mic, releases it immediately, then closes this window.
navigator.mediaDevices.getUserMedia({ audio: true, video: false })
  .then(stream => {
    stream.getTracks().forEach(t => t.stop());
    window.close();
  })
  .catch(() => {
    window.close();
  });
