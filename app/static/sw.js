/* Service worker. Eneste opgave er at tage imod push og åbne websitet.
   Vi cacher ikke noget — siden er server-renderet og skal altid være frisk,
   og en cache ville betyde at familien kunne stå i butikken med en gammel
   indkøbsliste. */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (e) => {
  let d = { titel: "Madplan", tekst: "Der er nyt i madplanen.", sti: "/" };
  try {
    if (e.data) d = { ...d, ...e.data.json() };
  } catch (_) {
    if (e.data) d.tekst = e.data.text();
  }

  e.waitUntil(
    self.registration.showNotification(d.titel, {
      body: d.tekst,
      icon: "/static/ikon-192.png",
      badge: "/static/ikon-192.png",
      lang: "da",
      /* Samme tag betyder at en ny besked erstatter den gamle i stedet for
         at lægge sig oveni. Ingen skal vågne til fem notifikationer. */
      tag: "madplan",
      renotify: true,
      data: { sti: d.sti || "/" },
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const maal = new URL(e.notification.data?.sti || "/", self.location.origin).href;

  /* Har familien allerede siden åbne, så gå derhen i det vindue frem for
     at åbne endnu et. */
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((vinduer) => {
      for (const v of vinduer) {
        if (v.url.startsWith(self.location.origin) && "focus" in v) {
          v.navigate(maal);
          return v.focus();
        }
      }
      return self.clients.openWindow(maal);
    })
  );
});
