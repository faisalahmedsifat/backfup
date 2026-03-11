CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE posts (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    title TEXT NOT NULL,
    body TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO users (name, email) VALUES
    ('Alice Johnson', 'alice@example.com'),
    ('Bob Smith', 'bob@example.com'),
    ('Carol White', 'carol@example.com');

INSERT INTO posts (user_id, title, body) VALUES
    (1, 'First Post', 'Hello from Alice'),
    (1, 'Second Post', 'Another post from Alice'),
    (2, 'Bob''s Post', 'Hello from Bob'),
    (3, 'Carol''s Intro', 'Hi everyone, Carol here');