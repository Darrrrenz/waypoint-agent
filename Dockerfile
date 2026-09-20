FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.17
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY fixtures ./fixtures
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["waypoint-agent"]
CMD ["run"]
