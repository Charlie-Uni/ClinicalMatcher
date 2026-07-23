import torch
from transformers import AutoTokenizer, AutoModel

device = "cuda" if torch.cuda.is_available() else "cpu"
print("Using device:", device)

# 1. 加载 Query Encoder（给“问题”用）
print("Loading MedCPT Query Encoder ...")
q_model_name = "ncbi/MedCPT-Query-Encoder"
q_tokenizer = AutoTokenizer.from_pretrained(q_model_name)
q_model = AutoModel.from_pretrained(q_model_name).to(device)
q_model.eval()

# 2. 加载 Article Encoder（给“病历文本”用）
print("Loading MedCPT Article Encoder ...")
d_model_name = "ncbi/MedCPT-Article-Encoder"
d_tokenizer = AutoTokenizer.from_pretrained(d_model_name)
d_model = AutoModel.from_pretrained(d_model_name).to(device)
d_model.eval()

# 3. 随便来一个问题 + 一小段文本，跑一下 embedding 看看
query = "Does this patient have a history of hemorrhagic stroke?"
doc = "The patient has a recent right MCA infarct with hemorrhagic transformation after tPA."

# 编码 query
q_inputs = q_tokenizer(
    [query],
    padding=True,
    truncation=True,
    max_length=64,
    return_tensors="pt",
).to(device)

with torch.no_grad():
    q_outputs = q_model(**q_inputs)
    q_embed = q_outputs.last_hidden_state[:, 0, :]  # [CLS] 向量

# 编码文档
d_inputs = d_tokenizer(
    [doc],
    padding=True,
    truncation=True,
    max_length=256,
    return_tensors="pt",
).to(device)

with torch.no_grad():
    d_outputs = d_model(**d_inputs)
    d_embed = d_outputs.last_hidden_state[:, 0, :]

# 算一下相似度
score = torch.matmul(q_embed, d_embed.T).item()

print("query embedding shape:", q_embed.shape)
print("doc embedding shape:", d_embed.shape)
print("similarity score:", score)
