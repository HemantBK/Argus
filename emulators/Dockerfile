# Firebase Local Emulator Suite for development: Firebase Auth + Firestore with firestore.rules enforced.
# Node runs firebase-tools; the Firestore emulator is a Java program and firebase-tools needs Java 21+,
# taken from the official Eclipse Temurin JRE image.
FROM eclipse-temurin:21.0.5_11-jre AS java

FROM node:22.12.0-bookworm-slim
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="${JAVA_HOME}/bin:${PATH}"
COPY --from=java /opt/java/openjdk /opt/java/openjdk
RUN npm install -g firebase-tools@15.34.0 && firebase setup:emulators:firestore
WORKDIR /srv
COPY firebase.json firestore.rules ./
EXPOSE 8081 9099
CMD ["firebase", "emulators:start", "--only", "auth,firestore", "--project", "argus-local"]
