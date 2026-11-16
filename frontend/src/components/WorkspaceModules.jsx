import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { useDispatch } from 'react-redux';
import { fetchHistory } from '../store/complaintSlice';

const API_BASE = 'http://localhost:8000/api';

const cardStyle = {
    backgroundColor: 'var(--c-172438)',
    border: '1px solid var(--c-30415a)',
    borderRadius: '13px',
    padding: '22px',
    boxShadow: '0 8px 24px rgba(0,0,0,.14)',
};

const inputStyle = {
    width: '100%',
    padding: '11px 13px',
    borderRadius: '8px',
    border: '1px solid var(--c-34465f)',
    backgroundColor: 'var(--c-111e30)',
    color: 'var(--c-f2f5fa)',
    fontSize: '13px',
    fontFamily: 'inherit',
    outline: 'none',
    boxSizing: 'border-box',
};

const btnPrimary = {
    padding: '10px 16px',
    borderRadius: '8px',
    border: 'none',
    backgroundColor: 'var(--c-7968b8)',
    color: '#fff',
    fontSize: '13px',
    fontWeight: '700',
    cursor: 'pointer',
};

const btnSecondary = {
    padding: '8px 13px',
    borderRadius: '8px',
    border: '1px solid var(--c-3a4b65)',
    backgroundColor: 'var(--c-111e30)',
    color: 'var(--c-d6deea)',
    fontSize: '12px',
    fontWeight: '600',
    cursor: 'pointer',
};

export const MyComplaintsView = ({ onOpenInCopilot }) => {
    const dispatch = useDispatch();
    const [complaints, setComplaints] = useState([]);
    const [batches, setBatches] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [selectedBatchMap, setSelectedBatchMap] = useState({});
    const [investigationMap, setInvestigationMap] = useState({});
    const [busyId, setBusyId] = useState(null);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [compRes, batchRes] = await Promise.all([
                axios.get(`${API_BASE}/complaints`),
                axios.get(`${API_BASE}/batches/`),
            ]);
            setComplaints(compRes.data || []);
            setBatches(batchRes.data || []);
        } catch (err) {
            setError(err.response?.data?.detail || 'Failed to load complaints from backend.');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        loadData();
    }, [loadData]);

    const handleLoadHistory = async (complaintId) => {
        await dispatch(fetchHistory(complaintId));
        if (onOpenInCopilot) onOpenInCopilot();
    };

    const handleLinkBatch = async (complaintId) => {
        const batchId = selectedBatchMap[complaintId];
        if (!batchId) return;
        setBusyId(`link-${complaintId}`);
        try {
            const res = await axios.post(`${API_BASE}/investigations/${complaintId}/link`, {
                batch_id: Number(batchId),
            });
            setInvestigationMap((prev) => ({ ...prev, [complaintId]: res.data }));
            await loadData();
        } catch (err) {
            alert(err.response?.data?.detail || 'Failed to link batch.');
        } finally {
            setBusyId(null);
        }
    };

    const handleAnalyze = async (complaintId) => {
        setBusyId(`analyze-${complaintId}`);
        try {
            const res = await axios.post(`${API_BASE}/investigations/${complaintId}/analyze`);
            setInvestigationMap((prev) => ({ ...prev, [complaintId]: res.data }));
        } catch (err) {
            alert(err.response?.data?.detail || 'AI Investigation failed.');
        } finally {
            setBusyId(null);
        }
    };

    if (loading) {
        return <div style={{ padding: '24px', color: 'var(--c-aab7c9)' }}>Loading complaints from QMS Ledger...</div>;
    }

    return (
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ color: 'var(--c-aab7c9)', fontSize: '13px' }}>
                    Showing <strong>{complaints.length}</strong> recorded complaint(s) from PostgreSQL Ledger
                </div>
                <button type="button" style={btnSecondary} onClick={loadData}>↻ Refresh</button>
            </div>

            {error && (
                <div style={{ padding: '14px', borderRadius: '8px', backgroundColor: 'var(--c-39252e)', color: 'var(--c-f2a3ad)', border: '1px solid var(--c-80505d)', fontSize: '13px' }}>
                    {error}
                </div>
            )}

            {complaints.length === 0 ? (
                <div style={{ ...cardStyle, textAlign: 'center', color: 'var(--c-9eacc0)', padding: '48px 24px' }}>
                    No complaints recorded yet. Use <strong>Log Complaint</strong> to submit a complaint via chat or PDF/Image upload.
                </div>
            ) : (
                complaints.map((c) => {
                    const inv = investigationMap[c.id];
                    const linkedBatchId = c.linked_batch_id || inv?.linked_batch_id;
                    return (
                        <div key={c.id} style={cardStyle}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px', flexWrap: 'wrap', marginBottom: '12px' }}>
                                <div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                                        <strong style={{ fontSize: '16px', color: 'var(--c-f2f5fa)' }}>
                                            {c.product_name || 'Unspecified Product'}
                                        </strong>
                                        <span style={{ fontSize: '12px', padding: '3px 9px', borderRadius: '6px', backgroundColor: 'var(--c-111e30)', border: '1px solid var(--c-34465f)', color: 'var(--c-b69cff)' }}>
                                            Batch: {c.batch_number || 'N/A'}
                                        </span>
                                        <span style={{ fontSize: '11px', padding: '3px 9px', borderRadius: '6px', backgroundColor: 'var(--c-153b35)', color: 'var(--c-55d6ac)', fontWeight: 700 }}>
                                            {c.status || 'Pending Triage'}
                                        </span>
                                        {c.severity && (
                                            <span style={{ fontSize: '11px', padding: '3px 9px', borderRadius: '6px', backgroundColor: 'var(--c-39252e)', color: 'var(--c-f2a3ad)', fontWeight: 700, textTransform: 'uppercase' }}>
                                                Severity: {c.severity}
                                            </span>
                                        )}
                                    </div>
                                    <div style={{ fontSize: '12px', color: 'var(--c-9eacc0)', marginTop: '5px' }}>
                                        Ref ID: <code>{c.id}</code> · Customer: {c.customer_name || 'N/A'} ({c.location || 'N/A'}) · Source: {c.source || 'chat'}
                                    </div>
                                </div>
                                <button type="button" style={btnSecondary} onClick={() => handleLoadHistory(c.id)}>
                                    Open in Copilot ↗
                                </button>
                            </div>

                            <p style={{ margin: '0 0 14px', fontSize: '13px', color: 'var(--c-d6deea)', lineHeight: 1.55 }}>
                                {c.description || c.summary || 'No description provided yet.'}
                            </p>

                            {/* QA Investigation & Batch Linking Bar */}
                            <div style={{ borderTop: '1px solid var(--c-30415a)', paddingTop: '14px', marginTop: '10px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                    <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--c-aab7c9)' }}>QA Batch Link:</span>
                                    <select
                                        style={{ ...inputStyle, width: 'auto', minWidth: '210px', padding: '7px 10px', fontSize: '12px' }}
                                        value={selectedBatchMap[c.id] ?? (linkedBatchId || '')}
                                        onChange={(e) => setSelectedBatchMap((prev) => ({ ...prev, [c.id]: e.target.value }))}
                                    >
                                        <option value="">Select manufacturing batch...</option>
                                        {batches.map((b) => (
                                            <option key={b.id} value={b.id}>
                                                #{b.id} — {b.batch_number} ({b.product_name}) [{b.status}]
                                            </option>
                                        ))}
                                    </select>
                                    <button
                                        type="button"
                                        style={btnSecondary}
                                        disabled={busyId === `link-${c.id}`}
                                        onClick={() => handleLinkBatch(c.id)}
                                    >
                                        {busyId === `link-${c.id}` ? 'Linking...' : linkedBatchId ? 'Update Linked Batch' : 'Link Batch'}
                                    </button>
                                </div>

                                <button
                                    type="button"
                                    style={{ ...btnPrimary, opacity: linkedBatchId ? 1 : 0.55 }}
                                    disabled={!linkedBatchId || busyId === `analyze-${c.id}`}
                                    onClick={() => handleAnalyze(c.id)}
                                    title={linkedBatchId ? 'Run AI Root Cause Analysis against batch history' : 'Link a batch first to run AI RCA'}
                                >
                                    {busyId === `analyze-${c.id}` ? 'Running AI RCA...' : '✦ Run AI Root Cause Analysis'}
                                </button>
                            </div>

                            {inv?.ai_analysis && (
                                <div style={{ marginTop: '14px', padding: '16px', borderRadius: '10px', backgroundColor: 'var(--c-111e30)', border: '1px solid var(--c-7568a8)' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                                        <strong style={{ color: 'var(--c-d0bdff)', fontSize: '13px' }}>✦ AI QA Investigation Report</strong>
                                        <span style={{ fontSize: '12px', color: 'var(--c-55d6ac)', fontWeight: 700 }}>
                                            Risk: {inv.ai_analysis.risk_level} · Confidence: {inv.ai_analysis.confidence_score}%
                                        </span>
                                    </div>
                                    <div style={{ fontSize: '13px', color: 'var(--c-e5ebf3)', marginBottom: '8px', lineHeight: 1.5 }}>
                                        <strong>Root Cause Analysis:</strong> {inv.ai_analysis.root_cause_analysis}
                                    </div>
                                    <div style={{ fontSize: '13px', color: 'var(--c-b8a8e8)', lineHeight: 1.5 }}>
                                        <strong>CAPA Recommendation:</strong> {inv.ai_analysis.capa_recommendation}
                                    </div>
                                </div>
                            )}
                        </div>
                    );
                })
            )}
        </div>
    );
};

export const BatchesProductionView = () => {
    const [batches, setBatches] = useState([]);
    const [orgs, setOrgs] = useState([]);
    const [selectedBatch, setSelectedBatch] = useState(null);
    const [stages, setStages] = useState([]);
    const [documents, setDocuments] = useState([]);
    const [notice, setNotice] = useState('');

    // Forms
    const [batchForm, setBatchForm] = useState({
        batch_number: '',
        product_name: '',
        manufacturing_unit_id: '',
        manufacturing_date: '',
        expiry_date: '',
    });
    const [stageForm, setStageForm] = useState({ stage_name: 'Dispensing', notes: '' });
    const [docType, setDocType] = useState('BMR');
    const [docFile, setDocFile] = useState(null);

    const loadBatchesAndUnits = useCallback(async () => {
        try {
            const [bRes, oRes] = await Promise.all([
                axios.get(`${API_BASE}/batches/`),
                axios.get(`${API_BASE}/auth/organizations`),
            ]);
            setBatches(bRes.data || []);
            setOrgs(oRes.data || []);
            const firstUnit = oRes.data?.[0]?.units?.[0]?.id;
            if (firstUnit) {
                setBatchForm((prev) => ({ ...prev, manufacturing_unit_id: prev.manufacturing_unit_id || String(firstUnit) }));
            }
        } catch (err) {
            setNotice(err.response?.data?.detail || 'Could not load batches.');
        }
    }, []);

    useEffect(() => {
        loadBatchesAndUnits();
    }, [loadBatchesAndUnits]);

    const loadBatchDetails = async (batch) => {
        setSelectedBatch(batch);
        try {
            const [sRes, dRes] = await Promise.all([
                axios.get(`${API_BASE}/production/batches/${batch.id}/stages`),
                axios.get(`${API_BASE}/production/batches/${batch.id}/documents`),
            ]);
            setStages(sRes.data || []);
            setDocuments(dRes.data || []);
        } catch (err) {
            setNotice('Failed to load production stages/documents.');
        }
    };

    const handleCreateBatch = async (e) => {
        e.preventDefault();
        setNotice('');
        try {
            const res = await axios.post(`${API_BASE}/batches/`, {
                ...batchForm,
                manufacturing_unit_id: Number(batchForm.manufacturing_unit_id),
            });
            setBatchForm((prev) => ({ ...prev, batch_number: '', product_name: '', manufacturing_date: '', expiry_date: '' }));
            setNotice(`Batch ${res.data.batch_number} created successfully.`);
            await loadBatchesAndUnits();
            loadBatchDetails(res.data);
        } catch (err) {
            setNotice(err.response?.data?.detail || 'Failed to create batch.');
        }
    };

    const handleUpdateBatchStatus = async (batchId, status) => {
        try {
            const res = await axios.patch(`${API_BASE}/batches/${batchId}`, { status });
            await loadBatchesAndUnits();
            if (selectedBatch?.id === batchId) setSelectedBatch(res.data);
        } catch (err) {
            setNotice('Failed to update batch status.');
        }
    };

    const handleAddStage = async (e) => {
        e.preventDefault();
        if (!selectedBatch) return;
        try {
            await axios.post(`${API_BASE}/production/stages`, {
                batch_id: selectedBatch.id,
                stage_name: stageForm.stage_name,
                notes: stageForm.notes,
            });
            setStageForm({ stage_name: 'Dispensing', notes: '' });
            await loadBatchDetails(selectedBatch);
        } catch (err) {
            setNotice(err.response?.data?.detail || 'Failed to add production stage.');
        }
    };

    const handleStageStatus = async (stageId, status) => {
        if (!selectedBatch) return;
        try {
            await axios.patch(`${API_BASE}/production/stages/${stageId}`, { status });
            await loadBatchDetails(selectedBatch);
        } catch (err) {
            setNotice('Failed to update stage status.');
        }
    };

    const handleUploadDoc = async (e) => {
        e.preventDefault();
        if (!selectedBatch || !docFile) return;
        try {
            const formData = new FormData();
            formData.append('document_type', docType);
            formData.append('file', docFile);
            await axios.post(`${API_BASE}/production/batches/${selectedBatch.id}/documents`, formData, {
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            setDocFile(null);
            await loadBatchDetails(selectedBatch);
        } catch (err) {
            setNotice(err.response?.data?.detail || 'Failed to upload batch document.');
        }
    };

    const allUnits = orgs.flatMap((o) =>
        (o.units || []).map((u) => ({ ...u, orgName: o.legal_name }))
    );

    return (
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 24px 24px', display: 'grid', gridTemplateColumns: '1fr 1.25fr', gap: '18px' }}>
            {/* Left column: Create Batch & Batch List */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div style={cardStyle}>
                    <h3 style={{ margin: '0 0 14px', fontSize: '15px', color: 'var(--c-f2f5fa)' }}>Create Production Batch</h3>
                    {notice && (
                        <div style={{ marginBottom: '12px', padding: '10px', borderRadius: '8px', backgroundColor: 'var(--c-111e30)', color: 'var(--c-b69cff)', fontSize: '12px' }}>
                            {notice}
                        </div>
                    )}
                    <form onSubmit={handleCreateBatch} style={{ display: 'grid', gap: '10px' }}>
                        <input
                            style={inputStyle}
                            placeholder="Batch Number (e.g. BMX240603)"
                            value={batchForm.batch_number}
                            onChange={(e) => setBatchForm({ ...batchForm, batch_number: e.target.value })}
                            required
                        />
                        <input
                            style={inputStyle}
                            placeholder="Product Name (e.g. Amoxicillin 500mg)"
                            value={batchForm.product_name}
                            onChange={(e) => setBatchForm({ ...batchForm, product_name: e.target.value })}
                            required
                        />
                        <select
                            style={inputStyle}
                            value={batchForm.manufacturing_unit_id}
                            onChange={(e) => setBatchForm({ ...batchForm, manufacturing_unit_id: e.target.value })}
                            required
                        >
                            <option value="">Select Manufacturing Unit...</option>
                            {allUnits.map((u) => (
                                <option key={u.id} value={u.id}>
                                    {u.orgName} — {u.unit_name} ({u.unit_type})
                                </option>
                            ))}
                        </select>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                            <input
                                style={inputStyle}
                                placeholder="Mfg Date (e.g. Jan 2026)"
                                value={batchForm.manufacturing_date}
                                onChange={(e) => setBatchForm({ ...batchForm, manufacturing_date: e.target.value })}
                            />
                            <input
                                style={inputStyle}
                                placeholder="Expiry Date (e.g. Dec 2028)"
                                value={batchForm.expiry_date}
                                onChange={(e) => setBatchForm({ ...batchForm, expiry_date: e.target.value })}
                            />
                        </div>
                        <button type="submit" style={btnPrimary}>＋ Create Batch</button>
                    </form>
                </div>

                <div style={cardStyle}>
                    <h3 style={{ margin: '0 0 14px', fontSize: '15px', color: 'var(--c-f2f5fa)' }}>
                        Registered Batches ({batches.length})
                    </h3>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        {batches.map((b) => (
                            <div
                                key={b.id}
                                onClick={() => loadBatchDetails(b)}
                                style={{
                                    padding: '12px 14px',
                                    borderRadius: '10px',
                                    border: selectedBatch?.id === b.id ? '1px solid var(--c-7968b8)' : '1px solid var(--c-30415a)',
                                    backgroundColor: 'var(--c-111e30)',
                                    cursor: 'pointer',
                                    display: 'flex',
                                    justifyContent: 'space-between',
                                    alignItems: 'center',
                                }}
                            >
                                <div>
                                    <strong style={{ color: 'var(--c-f2f5fa)', fontSize: '14px' }}>{b.batch_number}</strong>
                                    <div style={{ color: 'var(--c-9eacc0)', fontSize: '12px' }}>{b.product_name}</div>
                                </div>
                                <select
                                    style={{ ...inputStyle, width: 'auto', padding: '5px 8px', fontSize: '11px' }}
                                    value={b.status}
                                    onClick={(e) => e.stopPropagation()}
                                    onChange={(e) => handleUpdateBatchStatus(b.id, e.target.value)}
                                >
                                    <option value="Active">Active</option>
                                    <option value="Quarantined">Quarantined</option>
                                    <option value="Released">Released</option>
                                </select>
                            </div>
                        ))}
                        {batches.length === 0 && (
                            <div style={{ color: 'var(--c-9eacc0)', fontSize: '13px' }}>No batches created yet.</div>
                        )}
                    </div>
                </div>
            </div>

            {/* Right column: Selected Batch Production Stages & Batch Documents */}
            <div style={cardStyle}>
                {!selectedBatch ? (
                    <div style={{ color: 'var(--c-9eacc0)', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>
                        Select a batch on the left to manage its <strong>Production Stages</strong> and <strong>BMR / Analytical Documents</strong>.
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                        <div style={{ borderBottom: '1px solid var(--c-30415a)', paddingBottom: '12px' }}>
                            <h3 style={{ margin: 0, color: 'var(--c-f2f5fa)', fontSize: '16px' }}>
                                Batch {selectedBatch.batch_number} — {selectedBatch.product_name}
                            </h3>
                            <small style={{ color: 'var(--c-9eacc0)' }}>
                                Mfg: {selectedBatch.manufacturing_date || 'N/A'} · Exp: {selectedBatch.expiry_date || 'N/A'} · Status: {selectedBatch.status}
                            </small>
                        </div>

                        {/* Add Production Stage */}
                        <div>
                            <h4 style={{ margin: '0 0 10px', fontSize: '13px', color: 'var(--c-aab7c9)', textTransform: 'uppercase' }}>
                                Production Stages ({stages.length})
                            </h4>
                            <form onSubmit={handleAddStage} style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr auto', gap: '8px', marginBottom: '12px' }}>
                                <select
                                    style={inputStyle}
                                    value={stageForm.stage_name}
                                    onChange={(e) => setStageForm({ ...stageForm, stage_name: e.target.value })}
                                >
                                    <option value="Dispensing">Dispensing</option>
                                    <option value="Granulation">Granulation</option>
                                    <option value="Compression">Compression</option>
                                    <option value="Coating">Coating</option>
                                    <option value="Packaging">Packaging</option>
                                </select>
                                <input
                                    style={inputStyle}
                                    placeholder="Stage notes / deviation observations..."
                                    value={stageForm.notes}
                                    onChange={(e) => setStageForm({ ...stageForm, notes: e.target.value })}
                                />
                                <button type="submit" style={btnPrimary}>Add</button>
                            </form>

                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                {stages.map((s) => (
                                    <div key={s.id} style={{ padding: '10px 12px', borderRadius: '8px', backgroundColor: 'var(--c-111e30)', border: '1px solid var(--c-30415a)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px' }}>
                                        <div>
                                            <strong style={{ fontSize: '13px', color: 'var(--c-f2f5fa)' }}>{s.stage_name}</strong>
                                            {s.notes && <div style={{ fontSize: '12px', color: 'var(--c-9eacc0)', marginTop: '2px' }}>{s.notes}</div>}
                                        </div>
                                        <select
                                            style={{ ...inputStyle, width: 'auto', padding: '5px 8px', fontSize: '11px' }}
                                            value={s.status}
                                            onChange={(e) => handleStageStatus(s.id, e.target.value)}
                                        >
                                            <option value="Pending">Pending</option>
                                            <option value="In Progress">In Progress</option>
                                            <option value="Completed">Completed</option>
                                            <option value="Deviated">Deviated</option>
                                        </select>
                                    </div>
                                ))}
                            </div>
                        </div>

                        {/* Upload Batch Documents */}
                        <div style={{ borderTop: '1px solid var(--c-30415a)', paddingTop: '16px' }}>
                            <h4 style={{ margin: '0 0 10px', fontSize: '13px', color: 'var(--c-aab7c9)', textTransform: 'uppercase' }}>
                                Batch Manufacturing & Analytical Documents ({documents.length})
                            </h4>
                            <form onSubmit={handleUploadDoc} style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr auto', gap: '8px', marginBottom: '12px' }}>
                                <select style={inputStyle} value={docType} onChange={(e) => setDocType(e.target.value)}>
                                    <option value="BMR">BMR (Batch Mfg Record)</option>
                                    <option value="Analytical Report">Analytical Report (COA)</option>
                                    <option value="Equipment Logbook">Equipment Logbook</option>
                                    <option value="Stability Data">Stability Data</option>
                                </select>
                                <input
                                    type="file"
                                    style={{ ...inputStyle, padding: '8px' }}
                                    onChange={(e) => setDocFile(e.target.files?.[0] || null)}
                                    required
                                />
                                <button type="submit" style={btnPrimary}>Upload</button>
                            </form>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                {documents.map((d) => (
                                    <div key={d.id} style={{ padding: '8px 12px', borderRadius: '8px', backgroundColor: 'var(--c-111e30)', fontSize: '12px', color: 'var(--c-d6deea)', display: 'flex', justifyContent: 'space-between' }}>
                                        <span>📄 <strong>{d.document_type}</strong> — {d.file_name}</span>
                                        <span style={{ color: 'var(--c-9eacc0)' }}>{new Date(d.uploaded_at).toLocaleDateString()}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

export const AnalyticsView = () => {
    const [complaints, setComplaints] = useState([]);
    const [batches, setBatches] = useState([]);

    useEffect(() => {
        Promise.all([
            axios.get(`${API_BASE}/complaints`).catch(() => ({ data: [] })),
            axios.get(`${API_BASE}/batches/`).catch(() => ({ data: [] })),
        ]).then(([cRes, bRes]) => {
            setComplaints(cRes.data || []);
            setBatches(bRes.data || []);
        });
    }, []);

    const committedCount = complaints.filter((c) => c.status === 'Committed').length;
    const highSeverityCount = complaints.filter((c) => ['high', 'critical'].includes((c.severity || '').toLowerCase())).length;
    const quarantinedBatches = batches.filter((b) => b.status === 'Quarantined').length;

    return (
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '14px' }}>
                <div style={cardStyle}>
                    <small style={{ color: 'var(--c-9eacc0)', fontSize: '12px' }}>TOTAL COMPLAINTS</small>
                    <h2 style={{ margin: '8px 0 4px', fontSize: '28px', color: 'var(--c-f2f5fa)' }}>{complaints.length}</h2>
                    <span style={{ fontSize: '12px', color: 'var(--c-55d6ac)' }}>Live database count</span>
                </div>
                <div style={cardStyle}>
                    <small style={{ color: 'var(--c-9eacc0)', fontSize: '12px' }}>COMMITTED TO LEDGER</small>
                    <h2 style={{ margin: '8px 0 4px', fontSize: '28px', color: 'var(--c-f2f5fa)' }}>{committedCount}</h2>
                    <span style={{ fontSize: '12px', color: 'var(--c-b69cff)' }}>{complaints.length - committedCount} in triage/draft</span>
                </div>
                <div style={cardStyle}>
                    <small style={{ color: 'var(--c-9eacc0)', fontSize: '12px' }}>HIGH / CRITICAL SEVERITY</small>
                    <h2 style={{ margin: '8px 0 4px', fontSize: '28px', color: 'var(--c-f2a3ad)' }}>{highSeverityCount}</h2>
                    <span style={{ fontSize: '12px', color: 'var(--c-f0b45c)' }}>Requires priority QA review</span>
                </div>
                <div style={cardStyle}>
                    <small style={{ color: 'var(--c-9eacc0)', fontSize: '12px' }}>TRACKED BATCHES</small>
                    <h2 style={{ margin: '8px 0 4px', fontSize: '28px', color: 'var(--c-f2f5fa)' }}>{batches.length}</h2>
                    <span style={{ fontSize: '12px', color: 'var(--c-f2a3ad)' }}>{quarantinedBatches} quarantined</span>
                </div>
            </div>
        </div>
    );
};

export const SettingsView = ({ currentUser, onSignOut }) => {
    const [backendAlive, setBackendAlive] = useState(null);

    useEffect(() => {
        axios.get('http://localhost:8000/')
            .then(() => setBackendAlive(true))
            .catch(() => setBackendAlive(false));
    }, []);

    return (
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div style={cardStyle}>
                <h3 style={{ margin: '0 0 12px', fontSize: '16px', color: 'var(--c-f2f5fa)' }}>Account & Organization Profile</h3>
                {currentUser ? (
                    <div style={{ display: 'grid', gap: '8px', fontSize: '13px', color: 'var(--c-d6deea)' }}>
                        <div><strong>Full Name:</strong> {currentUser.full_name}</div>
                        <div><strong>Email:</strong> {currentUser.email || 'N/A'}</div>
                        <div><strong>Assigned Role:</strong> <span style={{ color: 'var(--c-b69cff)', fontWeight: 700 }}>{currentUser.role}</span></div>
                        {currentUser.organization_name && (
                            <div><strong>Organization:</strong> {currentUser.organization_name} (ID: #{currentUser.organization_id})</div>
                        )}
                        {currentUser.registration_code && (
                            <div style={{ marginTop: '6px', padding: '10px 12px', borderRadius: '8px', backgroundColor: 'var(--c-111e30)', border: '1px solid var(--c-34465f)' }}>
                                <strong>Employee Registration Invite Code:</strong> <code style={{ color: 'var(--c-55d6ac)', fontWeight: 700 }}>{currentUser.registration_code}</code>
                                <div style={{ fontSize: '11px', color: 'var(--c-9eacc0)', marginTop: '4px' }}>
                                    Share this code and Organization ID (#{currentUser.organization_id}) with QA, QC, and Production employees so they can register under your organization.
                                </div>
                            </div>
                        )}
                        <div style={{ marginTop: '12px' }}>
                            <button type="button" style={btnSecondary} onClick={onSignOut}>Sign Out</button>
                        </div>
                    </div>
                ) : (
                    <div style={{ fontSize: '13px', color: 'var(--c-9eacc0)' }}>
                        Currently browsing in Guest Workspace mode. Sign in from the home page to link your user session.
                    </div>
                )}
            </div>

            <div style={cardStyle}>
                <h3 style={{ margin: '0 0 8px', fontSize: '15px', color: 'var(--c-f2f5fa)' }}>Backend API Connectivity</h3>
                <div style={{ fontSize: '13px', color: backendAlive ? 'var(--c-55d6ac)' : 'var(--c-f2a3ad)', fontWeight: 600 }}>
                    {backendAlive === null ? 'Checking http://localhost:8000 ...' : backendAlive ? '● Connected to FastAPI Backend (http://localhost:8000)' : '○ Backend unreachable at http://localhost:8000'}
                </div>
            </div>
        </div>
    );
};
