const CACHE_NAME = "ins-ei-shell-v5";
const PHOTO_CACHE = "ins-ei-photos-v1";

const APP_SHELL = [
  "/",
  "/index.html",
  "/styles.css",
  "/app.js",
  "/manifest.webmanifest"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_NAME && key !== PHOTO_CACHE)
          .map(key => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET") return;

  // Kundenfotos werden nach dem ersten Online-Aufruf lokal verfügbar gehalten.
  if (url.pathname.startsWith("/api/v1/customer-files/")) {
    event.respondWith(
      caches.open(PHOTO_CACHE).then(async cache => {
        try {
          const response = await fetch(request, { cache: "no-store" });
          if (response && response.ok) await cache.put(request, response.clone());
          return response;
        } catch (e) {
          const cached = await cache.match(request);
          return cached || Response.error();
        }
      })
    );
    return;
  }

  // Alle anderen API-Anfragen ausschließlich über das Netzwerk.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(request, { cache: "no-store" }));
    return;
  }

  event.respondWith(
    fetch(request)
      .then(response => {
        if (response && response.ok && response.type === "basic") {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(request);
        if (cached) return cached;

        if (request.mode === "navigate") {
          return caches.match("/index.html");
        }

        return Response.error();
      })
  );
});

self.addEventListener("push", event => {
  let data={title:"INS-EI",body:"Neue Benachrichtigung",url:"/"};
  try{data={...data,...event.data.json()}}catch(e){}
  event.waitUntil(self.registration.showNotification(data.title,{
    body:data.body,
    icon:"/icon-192.png",
    badge:"/icon-192.png",
    data:{url:data.url||"/"}
  }));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil(clients.matchAll({type:"window",includeUncontrolled:true}).then(list=>{
    for(const client of list){if("focus" in client)return client.focus()}
    return clients.openWindow(event.notification.data?.url||"/");
  }));
});
