FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
COPY styles.css /usr/share/nginx/html/styles.css
COPY app.js /usr/share/nginx/html/app.js
COPY offline.js /usr/share/nginx/html/offline.js
COPY manifest.webmanifest /usr/share/nginx/html/manifest.webmanifest
COPY icon-192.png /usr/share/nginx/html/icon-192.png
COPY icon-512.png /usr/share/nginx/html/icon-512.png
COPY apple-touch-icon.png /usr/share/nginx/html/apple-touch-icon.png
COPY sw.js /usr/share/nginx/html/sw.js
EXPOSE 80
