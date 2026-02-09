from app.models.company import Company

print("Company module:", Company.__module__)
print("Has email_templates?", hasattr(Company, "email_templates"))
print("Company attrs:", [a for a in dir(Company) if "template" in a])
