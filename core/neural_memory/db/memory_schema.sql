-- Hikari Neural Memory Schema v1.0
-- Memory stored in the private live brain SQLite database (not committed)

-- Enable features
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT (datetime('now')),
    description TEXT
);

-- Node types: PERSON, PROJECT, TOOL, PREFERENCE, FACT, SKILL, LOCATION, TOPIC, RESOURCE, EPISODE, CONCEPT, RULE, GOAL, ROUTINE
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_type TEXT NOT NULL CHECK(node_type IN (
        'PERSON', 'PROJECT', 'TOOL', 'PREFERENCE', 'FACT', 'SKILL',
        'LOCATION', 'TOPIC', 'RESOURCE', 'EPISODE', 'CONCEPT', 'RULE',
        'GOAL', 'ROUTINE', 'EVENT', 'CONVERSATION', 'DOCUMENT'
    )),
    name TEXT NOT NULL,
    alias TEXT,
    content TEXT,
    metadata TEXT, -- JSON blob for type-specific data
    salience REAL DEFAULT 0.5, -- 0.0-1.0 importance
    activation_count INTEGER DEFAULT 0,
    last_accessed TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    user_id TEXT DEFAULT 'local_user',
    is_archived INTEGER DEFAULT 0,
    is_pinned INTEGER DEFAULT 0,
    UNIQUE(node_type, name, user_id)
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_salience ON nodes(salience DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_last_accessed ON nodes(last_accessed);
CREATE INDEX IF NOT EXISTS idx_nodes_user ON nodes(user_id);

-- Edge types: KNOWS_ABOUT, PREFERS, WORKING_ON, USES_TOOL, LOCATED_AT, INTERESTED_IN, HAS_SKILL, LINKED_TO, PART_OF, MEMBER_OF, AUTHORED, FOLLOWS, CONTRADICTS, DERIVED_FROM
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    edge_type TEXT NOT NULL CHECK(edge_type IN (
        'KNOWS_ABOUT', 'PREFERS', 'WORKING_ON', 'USES_TOOL', 'LOCATED_AT',
        'INTERESTED_IN', 'HAS_SKILL', 'LINKED_TO', 'PART_OF', 'MEMBER_OF',
        'AUTHORED', 'FOLLOWS', 'CONTRADICTS', 'DERIVED_FROM', 'DEPENDS_ON',
        'COLLABORATES_WITH', 'INFLUENCED_BY', 'SUPPORTS', 'OPPOSES'
    )),
    weight REAL DEFAULT 1.0, -- Edge strength 0.0-1.0
    context TEXT, -- When/why this relationship exists
    bidirectional INTEGER DEFAULT 0, -- 1 if same relationship both ways
    last_accessed TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now')),
    user_id TEXT DEFAULT 'local_user',
    is_archived INTEGER DEFAULT 0,
    UNIQUE(source_id, target_id, edge_type, user_id)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_weight ON edges(weight DESC);

-- Episodes: conversation turns or events
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_type TEXT NOT NULL CHECK(episode_type IN (
        'CONVERSATION', 'INTERACTION', 'DISCOVERY', 'DECISION', 'PROBLEM', 'SOLUTION'
    )),
    title TEXT,
    summary TEXT,
    content TEXT, -- Full content or key exchanges
    nodes TEXT, -- JSON array of node IDs involved
    sentiment REAL, -- -1.0 to 1.0
    importance REAL DEFAULT 0.5,
    outcome TEXT, -- SUCCESS, FAILURE, IN_PROGRESS, UNKNOWN
    session_id TEXT,
    turn_count INTEGER DEFAULT 1,
    duration_seconds INTEGER,
    started_at TEXT DEFAULT (datetime('now')),
    ended_at TEXT,
    user_id TEXT DEFAULT 'local_user',
    is_archived INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_episodes_type ON episodes(episode_type);
CREATE INDEX IF NOT EXISTS idx_episodes_started ON episodes(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_importance ON episodes(importance DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);

-- Sessions: conversation sessions
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT UNIQUE NOT NULL,
    session_type TEXT DEFAULT 'CONVERSATION',
    started_at TEXT DEFAULT (datetime('now')),
    ended_at TEXT,
    turn_count INTEGER DEFAULT 0,
    user_id TEXT DEFAULT 'local_user',
    metadata TEXT -- JSON blob
);

CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);

-- Consolidation tracking
CREATE TABLE IF NOT EXISTS consolidation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consolidation_type TEXT NOT NULL, -- 'MICRO', 'BOUNDED', 'SESSION', 'FULL'
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    nodes_processed INTEGER DEFAULT 0,
    edges_processed INTEGER DEFAULT 0,
    episodes_processed INTEGER DEFAULT 0,
    errors TEXT,
    status TEXT DEFAULT 'RUNNING' -- RUNNING, COMPLETED, FAILED
);

CREATE INDEX IF NOT EXISTS idx_consolidation_type ON consolidation_log(consolidation_type);
CREATE INDEX IF NOT EXISTS idx_consolidation_started ON consolidation_log(started_at DESC);

-- User profile (seed data)
CREATE TABLE IF NOT EXISTS user_profile (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Memory stats for monitoring
CREATE TABLE IF NOT EXISTS memory_stats (
    date TEXT PRIMARY KEY,
    nodes_created INTEGER DEFAULT 0,
    edges_created INTEGER DEFAULT 0,
    episodes_created INTEGER DEFAULT 0,
    retrievals INTEGER DEFAULT 0,
    consolidations INTEGER DEFAULT 0,
    active_nodes INTEGER,
    active_edges INTEGER,
    avg_salience REAL
);

-- Full-text search virtual table for semantic fallback
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    name, content, metadata,
    content='nodes',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, name, content, metadata) VALUES (new.id, new.name, new.content, new.metadata);
END;

CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, name, content, metadata) VALUES('delete', old.id, old.name, old.content, old.metadata);
END;

CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, name, content, metadata) VALUES('delete', old.id, old.name, old.content, old.metadata);
    INSERT INTO nodes_fts(rowid, name, content, metadata) VALUES (new.id, new.name, new.content, new.metadata);
END;

-- Insert initial schema version
INSERT OR IGNORE INTO schema_version (version, description) VALUES (1, 'Initial neural memory schema');
