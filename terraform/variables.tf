variable "project_id"      { type = string }
variable "region"          { type = string  default = "us-central1" }
variable "ops_location"    { type = string  default = "US" }
variable "image"           { type = string  description = "Container image for review UI + workers" }
